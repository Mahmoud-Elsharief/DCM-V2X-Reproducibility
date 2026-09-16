from __future__ import annotations

"""Actual lossless wire codecs for SORA resource-status (RS) messages.

The codec mirrors the bit-length models in :mod:`sora.overhead`.  Main-paper
on-air states are 0=FREE, 1=ONE_HOP, 2=TWO_HOP.  The prepared future resource is advertised separately to direct one-hop neighbors
through SORA control metadata. It is deliberately absent from this RS codec and is not
relayed as an ordinary two-hop RS state.

A compact 2-byte common header is used in all encodings:
  bits 15..13 : method id (3 bits)
  bit  12     : mode (0=FULL/SNAPSHOT, 1=DELTA)
  bits 11..3  : entry/change count (9 bits, 0..511)
  bits 2..0   : codec version (currently 1)

Hybrid epoch/sequence metadata is returned separately because the simulator
already carries it in RS_meta and budgets 3 bytes for it on air.
"""

from dataclasses import dataclass
from typing import Mapping

from .overhead import (
    ResourceKey,
    compare_full_state_encodings,
    compare_delta_encodings,
    dense_state_vector,
    resource_space,
    _huffman_payload_bits,
    _best_rice_gap_bits,
)

_CODEC_VERSION = 1
_METHOD_TO_ID = {
    "raw2": 0,
    "dual_bitmap": 1,
    "occupancy_hop": 2,
    "sparse_index": 3,
    "rle_elias": 4,
    "huffman_state": 5,
    "rice_gap": 6,
    "change_bitmap": 7,
}
_ID_TO_METHOD = {v: k for k, v in _METHOD_TO_ID.items()}


class _BitWriter:
    def __init__(self):
        self.bits: list[int] = []

    def put(self, value: int, nbits: int) -> None:
        value = int(value)
        nbits = int(nbits)
        if nbits < 0 or value < 0 or (nbits and value >= (1 << nbits)):
            raise ValueError(f"value={value} does not fit in {nbits} bits")
        for shift in range(nbits - 1, -1, -1):
            self.bits.append((value >> shift) & 1)

    def put_bit(self, bit: int) -> None:
        self.bits.append(1 if bit else 0)

    def to_bytes(self) -> bytes:
        out = bytearray((len(self.bits) + 7) // 8)
        for i, bit in enumerate(self.bits):
            if bit:
                out[i // 8] |= 1 << (7 - (i % 8))
        return bytes(out)

    @property
    def nbits(self) -> int:
        return len(self.bits)


class _BitReader:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.pos = 0

    def get_bit(self) -> int:
        if self.pos >= 8 * len(self.payload):
            raise ValueError("unexpected end of RS bitstream")
        b = (self.payload[self.pos // 8] >> (7 - (self.pos % 8))) & 1
        self.pos += 1
        return b

    def get(self, nbits: int) -> int:
        value = 0
        for _ in range(int(nbits)):
            value = (value << 1) | self.get_bit()
        return value


def _idx_to_key(idx: int, sc: int) -> ResourceKey:
    return (int(idx) // int(sc), int(idx) % int(sc))


def _elias_gamma_write(w: _BitWriter, n: int) -> None:
    n = int(n)
    if n <= 0:
        raise ValueError("Elias gamma requires n>=1")
    b = bin(n)[2:]
    for _ in range(len(b) - 1):
        w.put_bit(0)
    for ch in b:
        w.put_bit(ch == "1")


def _elias_gamma_read(r: _BitReader) -> int:
    zeros = 0
    while r.get_bit() == 0:
        zeros += 1
    value = 1
    for _ in range(zeros):
        value = (value << 1) | r.get_bit()
    return value


def _rice_write(w: _BitWriter, value: int, k: int) -> None:
    value = max(0, int(value)); k = max(0, int(k))
    q = value >> k
    for _ in range(q):
        w.put_bit(1)
    w.put_bit(0)
    if k:
        w.put(value & ((1 << k) - 1), k)


def _rice_read(r: _BitReader, k: int) -> int:
    q = 0
    while r.get_bit() == 1:
        q += 1
    rem = r.get(k) if k else 0
    return (q << k) | rem


def _canonical_codes(lengths: Mapping[int, int]) -> dict[int, tuple[int, int]]:
    """Return symbol -> (canonical_code, bit_length)."""
    items = sorted((int(length), int(sym)) for sym, length in lengths.items() if int(length) > 0)
    if not items:
        return {}
    code = 0
    prev_len = items[0][0]
    out: dict[int, tuple[int, int]] = {}
    for length, sym in items:
        code <<= (length - prev_len)
        out[sym] = (code, length)
        code += 1
        prev_len = length
    return out


def _header(method: str, mode: str, count: int) -> bytes:
    method_id = _METHOD_TO_ID[method]
    mode_bit = 1 if mode == "delta" else 0
    count = int(count)
    if not (0 <= count <= 511):
        raise ValueError("RS entry/change count must fit in 9 bits")
    value = (method_id << 13) | (mode_bit << 12) | (count << 3) | _CODEC_VERSION
    return value.to_bytes(2, "big")


def _parse_header(blob: bytes) -> tuple[str, str, int, bytes]:
    if len(blob) < 2:
        raise ValueError("RS wire message is shorter than the 2-byte header")
    value = int.from_bytes(blob[:2], "big")
    method_id = (value >> 13) & 0x7
    mode = "delta" if ((value >> 12) & 1) else "full"
    count = (value >> 3) & 0x1FF
    version = value & 0x7
    if version != _CODEC_VERSION:
        raise ValueError(f"unsupported RS codec version {version}")
    if method_id not in _ID_TO_METHOD:
        raise ValueError(f"unknown RS encoding method id {method_id}")
    return _ID_TO_METHOD[method_id], mode, count, blob[2:]


@dataclass(frozen=True)
class EncodedRS:
    wire: bytes
    mode: str
    requested_method: str
    selected_method: str
    payload_bits: int
    total_bits: int
    total_bytes: int


def encode_rs(
    states: Mapping[ResourceKey, int],
    *,
    mode: str,
    method: str,
    rri_s: float = 0.1,
    numerology: int = 0,
    bandwidth_mhz: int = 10,
) -> EncodedRS:
    mode = str(mode).lower()
    if mode == "snapshot":
        mode = "full"
    if mode not in ("full", "delta"):
        raise ValueError("mode must be full/snapshot/delta")
    requested = str(method).lower()

    _, sc, nres = resource_space(rri_s=rri_s, numerology=numerology, bandwidth_mhz=bandwidth_mhz)
    index_bits = max(1, (nres - 1).bit_length())

    if mode == "full":
        table = compare_full_state_encodings(states, rri_s=rri_s, numerology=numerology,
                                             bandwidth_mhz=bandwidth_mhz, ce_header_bytes=2,
                                             hybrid_meta_bytes=0)
        if requested not in table:
            raise ValueError(f"encoding {requested!r} is not valid for FULL")
        selected = table[requested].get("selected_method", requested)
        vector = dense_state_vector(states, subchannels_per_slot=sc, resource_count=nres)
        occupied = [(i, int(v)) for i, v in enumerate(vector) if int(v) != 0]
        count = len(occupied)
        w = _BitWriter()

        if selected == "raw2":
            for v in vector:
                w.put(int(v), 2)
        elif selected == "dual_bitmap":
            for target in (1, 2):
                for v in vector:
                    w.put_bit(int(v) == target)
        elif selected == "occupancy_hop":
            for v in vector:
                w.put_bit(int(v) != 0)
            for _, v in occupied:
                w.put_bit(int(v) == 2)
        elif selected == "sparse_index":
            for idx, v in occupied:
                w.put(idx, index_bits); w.put(v, 2)
        elif selected == "rle_elias":
            if vector:
                prev, run = vector[0], 1
                for v in vector[1:]:
                    if v == prev:
                        run += 1
                    else:
                        w.put(prev, 2); _elias_gamma_write(w, run)
                        prev, run = v, 1
                w.put(prev, 2); _elias_gamma_write(w, run)
        elif selected == "huffman_state":
            c1 = sum(1 for v in vector if v == 1)
            c2 = sum(1 for v in vector if v == 2)
            w.put(c1, index_bits); w.put(c2, index_bits)
            _, lengths = _huffman_payload_bits(vector)
            codes = _canonical_codes(lengths)
            if codes:
                for v in vector:
                    code, nbits = codes[int(v)]
                    w.put(code, nbits)
        elif selected == "rice_gap":
            indices = [i for i, _ in occupied]
            _, k = _best_rice_gap_bits(indices, 1)
            w.put(k, 4)
            gaps = [indices[0]] + [indices[i] - indices[i-1] - 1 for i in range(1, len(indices))] if indices else []
            for gap in gaps:
                _rice_write(w, gap, k)
            for _, v in occupied:
                w.put_bit(v == 2)
        else:
            raise ValueError(selected)

    else:
        table = compare_delta_encodings(states, rri_s=rri_s, numerology=numerology,
                                        bandwidth_mhz=bandwidth_mhz, ce_header_bytes=2,
                                        hybrid_meta_bytes=0)
        if requested not in table:
            raise ValueError(f"encoding {requested!r} is not valid for DELTA")
        selected = table[requested].get("selected_method", requested)
        entries = []
        for key, raw in states.items():
            slot, ch = int(key[0]), int(key[1])
            idx = slot * sc + ch
            if idx < 0 or idx >= nres or ch < 0 or ch >= sc:
                raise ValueError(f"resource {key!r} outside configured space")
            v = int(raw)
            if v not in (0, 1, 2):
                raise ValueError("DELTA state must be 0/1/2")
            entries.append((idx, v))
        entries.sort()
        count = len(entries)
        w = _BitWriter()

        if selected == "sparse_index":
            for idx, v in entries:
                w.put(idx, index_bits); w.put(v, 2)
        elif selected == "change_bitmap":
            changed = {idx: v for idx, v in entries}
            for idx in range(nres):
                w.put_bit(idx in changed)
            for idx, _ in entries:
                w.put(changed[idx], 2)
        elif selected == "rice_gap":
            indices = [i for i, _ in entries]
            _, k = _best_rice_gap_bits(indices, 2)
            w.put(k, 4)
            gaps = [indices[0]] + [indices[i] - indices[i-1] - 1 for i in range(1, len(indices))] if indices else []
            for gap in gaps:
                _rice_write(w, gap, k)
            for _, v in entries:
                w.put(v, 2)
        else:
            raise ValueError(selected)

    header = _header(selected, mode, count)
    wire = header + w.to_bytes()
    # Match the exact modeled bit count (2-byte header + non-byte-aligned payload).
    modeled = table[selected]
    if len(wire) != int(modeled["total_bytes"]):
        raise AssertionError(
            f"codec/model mismatch for {mode}/{selected}: wire={len(wire)} B, model={modeled['total_bytes']} B"
        )
    return EncodedRS(
        wire=wire, mode=mode, requested_method=requested, selected_method=selected,
        payload_bits=w.nbits, total_bits=16 + w.nbits, total_bytes=len(wire)
    )


def decode_rs(
    wire: bytes,
    *,
    rri_s: float = 0.1,
    numerology: int = 0,
    bandwidth_mhz: int = 10,
) -> tuple[str, str, dict[ResourceKey, int]]:
    method, mode, count, payload = _parse_header(bytes(wire))
    _, sc, nres = resource_space(rri_s=rri_s, numerology=numerology, bandwidth_mhz=bandwidth_mhz)
    index_bits = max(1, (nres - 1).bit_length())
    r = _BitReader(payload)

    if mode == "full":
        vector = [0] * nres
        if method == "raw2":
            vector = [r.get(2) for _ in range(nres)]
        elif method == "dual_bitmap":
            one = [r.get_bit() for _ in range(nres)]
            two = [r.get_bit() for _ in range(nres)]
            if any(a and b for a, b in zip(one, two)):
                raise ValueError("invalid dual bitmap: resource marked both one-hop and two-hop")
            vector = [1 if a else (2 if b else 0) for a, b in zip(one, two)]
        elif method == "occupancy_hop":
            occ = [r.get_bit() for _ in range(nres)]
            if sum(occ) != count:
                raise ValueError("occupancy count does not match RS header")
            hops = [r.get_bit() for _ in range(count)]
            it = iter(hops)
            vector = [(1 + next(it)) if flag else 0 for flag in occ]
        elif method == "sparse_index":
            seen = set()
            for _ in range(count):
                idx, v = r.get(index_bits), r.get(2)
                if idx >= nres or v not in (1, 2) or idx in seen:
                    raise ValueError("invalid sparse FULL entry")
                seen.add(idx); vector[idx] = v
        elif method == "rle_elias":
            out = []
            while len(out) < nres:
                v = r.get(2)
                if v not in (0, 1, 2):
                    raise ValueError("invalid RLE state")
                run = _elias_gamma_read(r)
                if len(out) + run > nres:
                    raise ValueError("RLE run exceeds RS vector")
                out.extend([v] * run)
            vector = out
        elif method == "huffman_state":
            c1, c2 = r.get(index_bits), r.get(index_bits)
            c0 = nres - c1 - c2
            if c0 < 0:
                raise ValueError("invalid Huffman state counts")
            synthetic = [0] * c0 + [1] * c1 + [2] * c2
            _, lengths = _huffman_payload_bits(synthetic)
            codes = _canonical_codes(lengths)
            if not codes:
                only = 0 if c0 else (1 if c1 else 2)
                vector = [only] * nres
            else:
                reverse = {(code, nbits): sym for sym, (code, nbits) in codes.items()}
                max_len = max(n for _, n in codes.values())
                vector = []
                while len(vector) < nres:
                    code = 0
                    found = None
                    for nbits in range(1, max_len + 1):
                        code = (code << 1) | r.get_bit()
                        if (code, nbits) in reverse:
                            found = reverse[(code, nbits)]
                            break
                    if found is None:
                        raise ValueError("invalid Huffman codeword")
                    vector.append(found)
                if (vector.count(1), vector.count(2)) != (c1, c2):
                    raise ValueError("Huffman decoded counts do not match header counts")
        elif method == "rice_gap":
            k = r.get(4)
            indices = []
            prev = -1
            for j in range(count):
                gap = _rice_read(r, k)
                idx = gap if j == 0 else prev + 1 + gap
                if idx >= nres:
                    raise ValueError("Rice FULL index outside RS space")
                indices.append(idx); prev = idx
            for idx in indices:
                vector[idx] = 2 if r.get_bit() else 1
        elif method == "change_bitmap":
            raise ValueError("change_bitmap is not a FULL encoding")
        else:
            raise ValueError(method)
        states = {_idx_to_key(i, sc): int(v) for i, v in enumerate(vector) if int(v) != 0}
        if len(states) != count:
            raise ValueError("decoded FULL occupancy does not match RS header")
        return mode, method, states

    # DELTA: explicit FREE=0 transitions must be retained.
    entries: list[tuple[int, int]] = []
    if method == "sparse_index":
        for _ in range(count):
            idx, v = r.get(index_bits), r.get(2)
            if idx >= nres or v not in (0, 1, 2):
                raise ValueError("invalid sparse DELTA entry")
            entries.append((idx, v))
    elif method == "change_bitmap":
        flags = [r.get_bit() for _ in range(nres)]
        if sum(flags) != count:
            raise ValueError("change bitmap count does not match RS header")
        vals = [r.get(2) for _ in range(count)]
        if any(v not in (0, 1, 2) for v in vals):
            raise ValueError("invalid DELTA state")
        vi = iter(vals)
        entries = [(idx, next(vi)) for idx, flag in enumerate(flags) if flag]
    elif method == "rice_gap":
        k = r.get(4)
        indices = []
        prev = -1
        for j in range(count):
            gap = _rice_read(r, k)
            idx = gap if j == 0 else prev + 1 + gap
            if idx >= nres:
                raise ValueError("Rice DELTA index outside RS space")
            indices.append(idx); prev = idx
        entries = [(idx, r.get(2)) for idx in indices]
        if any(v not in (0, 1, 2) for _, v in entries):
            raise ValueError("invalid Rice DELTA state")
    else:
        raise ValueError(f"{method} is not a DELTA encoding")

    if len({idx for idx, _ in entries}) != len(entries):
        raise ValueError("duplicate DELTA resource index")
    return mode, method, {_idx_to_key(idx, sc): int(v) for idx, v in entries}
