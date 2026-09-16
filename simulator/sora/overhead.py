from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
from collections import Counter
from typing import Mapping, Sequence

ResourceKey = tuple[int, int]


@dataclass(frozen=True)
class RSOverheadBudget:
    """Protocol-specific SORA RS control payload budget.

    This is an explicit simulation encoding assumption, not a claim that SORA
    defines a standardized 3GPP MAC CE. Standard NR sidelink SCI/control is
    common to NR-V2X and SORA and is handled by the common simulator path.
    This model accounts only for the *additional* SORA RS information.
    """
    slots_per_rri: int
    subchannels_per_slot: int
    resource_count: int
    resource_index_bits: int
    state_bits: int
    entry_bits: int
    max_entries: int
    entry_payload_bytes: int
    ce_header_bytes: int
    hybrid_meta_bytes: int
    total_bytes: int
    rs_mode: str


def _ceil_bytes(bits: int) -> int:
    return int(math.ceil(max(0, int(bits)) / 8.0))


def resource_space(
    *, rri_s: float, numerology: int, bandwidth_mhz: int
) -> tuple[int, int, int]:
    """Return (slots_per_rri, subchannels_per_slot, resource_count)."""
    slot_s = 0.001 / (2 ** int(numerology))
    slots_per_rri = max(1, int(round(float(rri_s) / slot_s)))
    subchannels_per_slot = (
        5 * max(1, int(bandwidth_mhz) // 10)
        if int(numerology) == 0
        else (2 if int(numerology) == 1 else 1)
        * max(1, int(bandwidth_mhz) // 10)
    )
    return slots_per_rri, subchannels_per_slot, slots_per_rri * subchannels_per_slot


def sparse_rs_bytes(
    *,
    entry_count: int,
    rri_s: float,
    numerology: int,
    bandwidth_mhz: int,
    state_bits: int = 2,
    ce_header_bytes: int = 2,
    hybrid_meta_bytes: int = 0,
) -> dict:
    """Byte count for a sparse resource-index + state encoding."""
    slots_per_rri, subchannels_per_slot, resource_count = resource_space(
        rri_s=rri_s, numerology=numerology, bandwidth_mhz=bandwidth_mhz
    )
    index_bits = max(1, int(math.ceil(math.log2(resource_count))))
    entry_bits = index_bits + int(state_bits)
    n = max(0, int(entry_count))
    entry_payload_bits = n * entry_bits
    entry_payload_bytes = _ceil_bytes(entry_payload_bits)
    total_bytes = (
        entry_payload_bytes
        + max(0, int(ce_header_bytes))
        + max(0, int(hybrid_meta_bytes))
    )
    return {
        "slots_per_rri": slots_per_rri,
        "subchannels_per_slot": subchannels_per_slot,
        "resource_count": resource_count,
        "resource_index_bits": index_bits,
        "state_bits": int(state_bits),
        "entry_bits": entry_bits,
        "entry_count": n,
        "entry_payload_bits": entry_payload_bits,
        "entry_payload_bytes": entry_payload_bytes,
        "ce_header_bytes": max(0, int(ce_header_bytes)),
        "hybrid_meta_bytes": max(0, int(hybrid_meta_bytes)),
        "total_bytes": total_bytes,
    }


def fixed_rs_ce_budget(
    *,
    rri_s: float,
    numerology: int,
    bandwidth_mhz: int,
    max_entries: int,
    state_bits: int = 2,
    ce_header_bytes: int = 2,
    hybrid_meta_bytes: int = 3,
    rs_mode: str = "hybrid",
) -> dict:
    """Fixed padded sparse SORA RS control payload used for PHY accounting."""
    mode = str(rs_mode).strip().lower()
    if mode not in ("hybrid", "snapshot"):
        raise ValueError("rs_mode must be hybrid or snapshot")
    meta = max(0, int(hybrid_meta_bytes)) if mode == "hybrid" else 0
    result = sparse_rs_bytes(
        entry_count=max_entries,
        rri_s=rri_s,
        numerology=numerology,
        bandwidth_mhz=bandwidth_mhz,
        state_bits=state_bits,
        ce_header_bytes=ce_header_bytes,
        hybrid_meta_bytes=meta,
    )
    result.update({
        "max_entries": max(0, int(max_entries)),
        "rs_mode": mode,
        "padded": True,
    })
    return result


def bitmap_rs_bytes(
    *,
    rri_s: float,
    numerology: int,
    bandwidth_mhz: int,
    state_bits: int = 2,
    ce_header_bytes: int = 2,
    hybrid_meta_bytes: int = 0,
) -> dict:
    """Reference raw full-vector state packing (2 bits/resource by default)."""
    slots_per_rri, subchannels_per_slot, resource_count = resource_space(
        rri_s=rri_s, numerology=numerology, bandwidth_mhz=bandwidth_mhz
    )
    payload_bits = resource_count * int(state_bits)
    payload_bytes = _ceil_bytes(payload_bits)
    total = payload_bytes + max(0, int(ce_header_bytes)) + max(0, int(hybrid_meta_bytes))
    return {
        "slots_per_rri": slots_per_rri,
        "subchannels_per_slot": subchannels_per_slot,
        "resource_count": resource_count,
        "state_bits": int(state_bits),
        "payload_bits": payload_bits,
        "payload_bytes": payload_bytes,
        "ce_header_bytes": max(0, int(ce_header_bytes)),
        "hybrid_meta_bytes": max(0, int(hybrid_meta_bytes)),
        "total_bytes": total,
    }


def overhead_percentages(application_bytes: int, extra_bytes: int) -> dict:
    app = max(0, int(application_bytes))
    extra = max(0, int(extra_bytes))
    total = app + extra
    return {
        "application_bytes": app,
        "extra_control_bytes": extra,
        "total_bytes": total,
        "overhead_vs_application_pct": (100.0 * extra / app) if app else None,
        "overhead_fraction_of_total_pct": (100.0 * extra / total) if total else None,
    }


# ---------------------------------------------------------------------------
# Lossless RS encoding comparison
# ---------------------------------------------------------------------------
# Main-paper states are 0=FREE, 1=ONE_HOP, 2=TWO_HOP. The prepared future resource is advertised separately to direct one-hop neighbors
# and therefore does not appear in the ordinary 0/1/2 RS vector or propagate to two hops.
# The 2-byte CE header is assumed to carry encoding-id plus payload/count/length
# information needed to parse variable-length representations. Hybrid adds a
# separate metadata budget (default 3 B) for FULL/DELTA mode + epoch/sequence.

FULL_ENCODING_METHODS = (
    "raw2",
    "dual_bitmap",
    "occupancy_hop",
    "sparse_index",
    "rle_elias",
    "huffman_state",
    "rice_gap",
    "adaptive",
)

DELTA_ENCODING_METHODS = (
    "sparse_index",
    "change_bitmap",
    "rice_gap",
    "adaptive",
)


def _resource_index(key: ResourceKey, subchannels_per_slot: int, resource_count: int) -> int:
    slot, channel = int(key[0]), int(key[1])
    idx = slot * int(subchannels_per_slot) + channel
    if slot < 0 or channel < 0 or channel >= int(subchannels_per_slot) or idx >= int(resource_count):
        raise ValueError(f"resource {key!r} is outside the configured RS space")
    return idx


def dense_state_vector(
    states: Mapping[ResourceKey, int],
    *,
    subchannels_per_slot: int,
    resource_count: int,
) -> list[int]:
    """Convert a sparse (slot,subchannel)->state map into a fixed state vector."""
    vector = [0] * int(resource_count)
    for key, raw in states.items():
        state = int(raw)
        if state not in (0, 1, 2):
            raise ValueError(
                f"on-air compression supports main-paper states 0/1/2; got {state} for {key}. "
                "The advertised future resource is carried separately and must not be encoded as an ordinary RS state."
            )
        vector[_resource_index(tuple(key), subchannels_per_slot, resource_count)] = state
    return vector


def _elias_gamma_bits(n: int) -> int:
    """Number of bits in Elias-gamma coding for positive integer n."""
    n = int(n)
    if n <= 0:
        raise ValueError("Elias gamma requires n >= 1")
    return 2 * int(math.floor(math.log2(n))) + 1


def _huffman_payload_bits(values: Sequence[int]) -> tuple[int, dict[int, int]]:
    """Optimal binary Huffman payload length and deterministic code lengths."""
    counts = Counter(int(v) for v in values)
    if not counts:
        return 0, {}
    if len(counts) == 1:
        # If the single-symbol count is signaled, the fixed vector length makes
        # the sequence completely known and no per-symbol payload bits are needed.
        only = next(iter(counts))
        return 0, {only: 0}

    heap = []
    serial = 0
    # Store (weight, serial, {symbol:length}) to ensure deterministic ties.
    for sym in sorted(counts):
        heapq.heappush(heap, (counts[sym], serial, {sym: 0}))
        serial += 1
    while len(heap) > 1:
        w1, _, l1 = heapq.heappop(heap)
        w2, _, l2 = heapq.heappop(heap)
        merged = {s: n + 1 for s, n in {**l1, **l2}.items()}
        heapq.heappush(heap, (w1 + w2, serial, merged))
        serial += 1
    _, _, lengths = heap[0]
    return sum(counts[s] * lengths[s] for s in counts), lengths


def _rice_unsigned_bits(value: int, k: int) -> int:
    """Golomb-Rice length for a non-negative integer."""
    value = max(0, int(value))
    k = max(0, int(k))
    q = value >> k
    return q + 1 + k  # unary quotient (q ones + zero) + k-bit remainder


def _best_rice_gap_bits(indices: Sequence[int], state_bits_per_entry: int) -> tuple[int, int]:
    """Return (payload_bits, best_k) for sorted index gaps + per-entry state bits."""
    if not indices:
        # The Golomb-Rice wire format always transmits the 4-bit Rice
        # parameter k, even when the FULL/DELTA set is empty.  Count those
        # bits so the analytical byte model exactly matches serialization.
        return 4, 0
    idx = sorted(int(x) for x in indices)
    gaps = [idx[0]] + [idx[i] - idx[i - 1] - 1 for i in range(1, len(idx))]
    best_bits = None
    best_k = 0
    # k=0..9 is enough for a 500-resource map, but derive from max gap generally.
    max_k = max(1, int(math.ceil(math.log2(max(gaps) + 2))))
    for k in range(0, max_k + 1):
        bits = 4  # signal Rice parameter k in the variable-length payload header
        bits += sum(_rice_unsigned_bits(g, k) for g in gaps)
        bits += len(idx) * int(state_bits_per_entry)
        if best_bits is None or bits < best_bits:
            best_bits, best_k = bits, k
    return int(best_bits or 0), int(best_k)


def _result(method: str, payload_bits: int, ce_header_bytes: int, hybrid_meta_bytes: int, **extra) -> dict:
    payload_bits = max(0, int(payload_bits))
    header_bits = 8 * (max(0, int(ce_header_bytes)) + max(0, int(hybrid_meta_bytes)))
    total_bits = payload_bits + header_bits
    out = {
        "method": str(method),
        "payload_bits": payload_bits,
        "payload_bytes": _ceil_bytes(payload_bits),
        "ce_header_bytes": max(0, int(ce_header_bytes)),
        "hybrid_meta_bytes": max(0, int(hybrid_meta_bytes)),
        "total_bits": total_bits,
        # Physical packet sizes are byte-granular; round the complete RS CE once.
        "total_bytes": _ceil_bytes(total_bits),
    }
    out.update(extra)
    return out


def compare_full_state_encodings(
    states: Mapping[ResourceKey, int],
    *,
    rri_s: float,
    numerology: int,
    bandwidth_mhz: int,
    ce_header_bytes: int = 2,
    hybrid_meta_bytes: int = 0,
) -> dict[str, dict]:
    """Compare lossless encodings for a complete 0/1/2 SORA RS vector.

    Missing mapping entries are FREE, so a sparse Python dictionary still
    represents a complete logical resource map.

    Methods:
      raw2          fixed two-bit state vector;
      dual_bitmap   one 500-bit ONE_HOP mask + one 500-bit TWO_HOP mask;
      occupancy_hop 1 occupancy bit/resource + 1 hop-class bit/non-free resource;
      sparse_index  fixed index+2-bit-state tuples for non-free resources;
      rle_elias     runs of equal states, each as 2 state bits + Elias-gamma length;
      huffman_state adaptive state Huffman code; two state counts are signaled;
      rice_gap      sorted occupied-index gaps with optimal Golomb-Rice k + 1 hop bit;
      adaptive      chooses the smallest total-byte representation above.
    """
    slots, sc, nres = resource_space(
        rri_s=rri_s, numerology=numerology, bandwidth_mhz=bandwidth_mhz
    )
    vector = dense_state_vector(states, subchannels_per_slot=sc, resource_count=nres)
    nonfree_indices = [i for i, v in enumerate(vector) if v != 0]
    nocc = len(nonfree_indices)
    index_bits = max(1, int(math.ceil(math.log2(nres))))

    results: dict[str, dict] = {}
    results["raw2"] = _result(
        "raw2", 2 * nres, ce_header_bytes, hybrid_meta_bytes,
        resource_count=nres, occupied_count=nocc,
    )
    results["dual_bitmap"] = _result(
        "dual_bitmap", 2 * nres, ce_header_bytes, hybrid_meta_bytes,
        resource_count=nres, occupied_count=nocc,
    )
    results["occupancy_hop"] = _result(
        "occupancy_hop", nres + nocc, ce_header_bytes, hybrid_meta_bytes,
        resource_count=nres, occupied_count=nocc,
    )
    results["sparse_index"] = _result(
        "sparse_index", nocc * (index_bits + 2), ce_header_bytes, hybrid_meta_bytes,
        resource_count=nres, occupied_count=nocc, resource_index_bits=index_bits,
    )

    # Run-length encode the exact ordered 3-state vector.
    rle_bits = 0
    run_count = 0
    if vector:
        prev = vector[0]
        length = 1
        for value in vector[1:]:
            if value == prev:
                length += 1
            else:
                rle_bits += 2 + _elias_gamma_bits(length)
                run_count += 1
                prev, length = value, 1
        rle_bits += 2 + _elias_gamma_bits(length)
        run_count += 1
    results["rle_elias"] = _result(
        "rle_elias", rle_bits, ce_header_bytes, hybrid_meta_bytes,
        resource_count=nres, occupied_count=nocc, run_count=run_count,
    )

    # Adaptive Huffman over 0/1/2 states. To reconstruct the deterministic tree,
    # signal counts for states 1 and 2; state-0 count follows from nres.
    huff_data_bits, lengths = _huffman_payload_bits(vector)
    huff_count_bits = 2 * index_bits
    results["huffman_state"] = _result(
        "huffman_state", huff_data_bits + huff_count_bits,
        ce_header_bytes, hybrid_meta_bytes,
        resource_count=nres, occupied_count=nocc,
        code_lengths=lengths, frequency_count_bits=huff_count_bits,
    )

    rice_bits, rice_k = _best_rice_gap_bits(nonfree_indices, 1)
    results["rice_gap"] = _result(
        "rice_gap", rice_bits, ce_header_bytes, hybrid_meta_bytes,
        resource_count=nres, occupied_count=nocc, rice_k=rice_k,
    )

    candidates = [k for k in results]
    best = min(candidates, key=lambda k: (results[k]["total_bytes"], results[k]["total_bits"], k))
    results["adaptive"] = dict(results[best])
    results["adaptive"].update({"method": "adaptive", "selected_method": best})
    return results


def compare_delta_encodings(
    changes: Mapping[ResourceKey, int],
    *,
    rri_s: float,
    numerology: int,
    bandwidth_mhz: int,
    ce_header_bytes: int = 2,
    hybrid_meta_bytes: int = 3,
) -> dict[str, dict]:
    """Compare lossless encodings for a Hybrid DELTA update.

    DELTA values may be 0, 1 or 2 because FREE transitions must be explicit.
    The receiver already knows unchanged values from the current FULL baseline.
    """
    _, sc, nres = resource_space(
        rri_s=rri_s, numerology=numerology, bandwidth_mhz=bandwidth_mhz
    )
    entries = []
    for key, raw in changes.items():
        state = int(raw)
        if state not in (0, 1, 2):
            raise ValueError(f"delta state must be 0/1/2, got {state}")
        entries.append((_resource_index(tuple(key), sc, nres), state))
    entries.sort()
    n = len(entries)
    index_bits = max(1, int(math.ceil(math.log2(nres))))

    results: dict[str, dict] = {}
    results["sparse_index"] = _result(
        "sparse_index", n * (index_bits + 2), ce_header_bytes, hybrid_meta_bytes,
        resource_count=nres, changed_count=n, resource_index_bits=index_bits,
    )
    # A complete change bitmap says which positions changed; then 2 bits carry
    # the new 0/1/2 state for each set position.
    results["change_bitmap"] = _result(
        "change_bitmap", nres + 2 * n, ce_header_bytes, hybrid_meta_bytes,
        resource_count=nres, changed_count=n,
    )
    rice_bits, rice_k = _best_rice_gap_bits([i for i, _ in entries], 2)
    results["rice_gap"] = _result(
        "rice_gap", rice_bits, ce_header_bytes, hybrid_meta_bytes,
        resource_count=nres, changed_count=n, rice_k=rice_k,
    )

    best = min(results, key=lambda k: (results[k]["total_bytes"], results[k]["total_bits"], k))
    results["adaptive"] = dict(results[best])
    results["adaptive"].update({"method": "adaptive", "selected_method": best})
    return results


def rs_encoding_bytes(
    states: Mapping[ResourceKey, int],
    *,
    packet_mode: str,
    encoding: str,
    rri_s: float,
    numerology: int,
    bandwidth_mhz: int,
    ce_header_bytes: int = 2,
    hybrid_meta_bytes: int = 3,
) -> dict:
    """Measure one FULL/SNAPSHOT or DELTA packet using the selected encoding."""
    mode = str(packet_mode).strip().lower()
    enc = str(encoding).strip().lower()
    if mode in ("full", "snapshot"):
        table = compare_full_state_encodings(
            states,
            rri_s=rri_s,
            numerology=numerology,
            bandwidth_mhz=bandwidth_mhz,
            ce_header_bytes=ce_header_bytes,
            hybrid_meta_bytes=hybrid_meta_bytes,
        )
    elif mode == "delta":
        table = compare_delta_encodings(
            states,
            rri_s=rri_s,
            numerology=numerology,
            bandwidth_mhz=bandwidth_mhz,
            ce_header_bytes=ce_header_bytes,
            hybrid_meta_bytes=hybrid_meta_bytes,
        )
    else:
        raise ValueError("packet_mode must be full/snapshot/delta")
    if enc not in table:
        raise ValueError(f"encoding {encoding!r} not valid for {mode}; choices={sorted(table)}")
    return table[enc]
