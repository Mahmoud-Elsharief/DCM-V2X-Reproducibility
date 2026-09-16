from __future__ import annotations

from dataclasses import dataclass
import math

# Published system-level anchors from Todisco et al., IEEE Access 2021,
# Table 3 (SCS 15 kHz). Only the MCS values used by SORA are frozen here.
# Entries are packet_bytes: {mcs: required PRBs}.
_PUBLISHED_NPRB = {
    190: {4: 25, 11: 12},
    350: {4: 44, 11: 20},
}

# Common compact resource budget used for the reconstructed comparison.
# This intentionally preserves the original simulator's 2/1-subchannel
# frequency occupation, but it is no longer restricted to the legacy path:
# both NR-V2X and SORA may use it under the common ``fixed`` sizing mode.
_FIXED_SUBCHANNELS = {4: 2, 11: 1}
_LEGACY_SUBCHANNELS = _FIXED_SUBCHANNELS  # backward-compatible alias
_LEGACY_THRESHOLDS_DB = {4: 2.10, 11: 10.13}


@dataclass(frozen=True)
class ResourceSizingResult:
    mode: str
    mcs_index: int
    packet_bytes: int
    subchannel_size_prb: int
    required_prb: int
    required_subchannels: int
    sinr_threshold_db: float | None
    method: str


def _linear_anchor_value(packet_bytes: int, mcs_index: int, table: dict[int, dict[int, float]]) -> float:
    b = int(packet_bytes)
    m = int(mcs_index)
    if b in table and m in table[b]:
        return float(table[b][m])
    sizes = sorted(k for k, row in table.items() if m in row)
    if len(sizes) < 2:
        raise KeyError(f"Insufficient published anchors for MCS {m}")
    lo, hi = sizes[0], sizes[-1]
    if not lo <= b <= hi:
        raise ValueError(
            f"Published interpolation is validated only for {lo}..{hi} bytes; got {b}. "
            "Use an explicit PHY/TBS calibration outside this range."
        )
    y0, y1 = float(table[lo][m]), float(table[hi][m])
    return y0 + (b - lo) * (y1 - y0) / (hi - lo)


def published_required_prb(mcs_index: int, packet_bytes: int) -> int:
    """Conservative PRB estimate between published 190/350-byte anchors.

    The simulator allocates in 10-PRB subchannel granularity.  For the 300-byte
    paper configuration, the interpolation gives 38.06 PRBs for MCS 4 and 17.5
    PRBs for MCS 11; ceiling before subchannel rounding gives 39 and 18 PRBs,
    which map unambiguously to 4 and 2 ten-PRB subchannels.
    """
    return int(math.ceil(_linear_anchor_value(packet_bytes, mcs_index, _PUBLISHED_NPRB) - 1e-12))


def fixed_required_subchannels(mcs_index: int | None, fallback: int = 1) -> int:
    if mcs_index is None:
        return int(fallback)
    return int(_FIXED_SUBCHANNELS.get(int(mcs_index), fallback))


def legacy_required_subchannels(mcs_index: int | None, fallback: int = 1) -> int:
    if mcs_index is None:
        return int(fallback)
    return int(_LEGACY_SUBCHANNELS.get(int(mcs_index), fallback))


def published_required_subchannels(
    mcs_index: int,
    packet_bytes: int,
    subchannel_size_prb: int = 10,
) -> int:
    if subchannel_size_prb <= 0:
        raise ValueError("subchannel_size_prb must be positive")
    nprb = published_required_prb(mcs_index, packet_bytes)
    return int(math.ceil(nprb / int(subchannel_size_prb)))


def legacy_sinr_threshold_db(mcs_index: int, fallback: float | None = None) -> float:
    if int(mcs_index) in _LEGACY_THRESHOLDS_DB:
        return float(_LEGACY_THRESHOLDS_DB[int(mcs_index)])
    if fallback is None:
        raise KeyError(f"No legacy threshold for MCS {mcs_index}")
    return float(fallback)


def resource_sizing(
    mode: str,
    mcs_index: int,
    packet_bytes: int,
    *,
    subchannel_size_prb: int = 10,
    published_threshold_db: float | None = None,
    legacy_threshold_fallback_db: float | None = None,
) -> ResourceSizingResult:
    mode = str(mode).strip().lower()
    if mode in ("compact", "fixed_2_1"):
        mode = "fixed"
    m = int(mcs_index)
    b = int(packet_bytes)
    if mode in ("fixed", "legacy"):
        sc = fixed_required_subchannels(m)
        return ResourceSizingResult(
            mode=mode,
            mcs_index=m,
            packet_bytes=b,
            subchannel_size_prb=int(subchannel_size_prb),
            required_prb=sc * int(subchannel_size_prb),
            required_subchannels=sc,
            sinr_threshold_db=legacy_sinr_threshold_db(m, legacy_threshold_fallback_db),
            method=(
                "common fixed 2/1-subchannel mapping for MCS4/MCS11"
                if mode == "fixed"
                else "supplied SORA/NR-V2X code mapping"
            ),
        )
    if mode in ("published", "reconstructed"):
        prb = published_required_prb(m, b)
        sc = int(math.ceil(prb / int(subchannel_size_prb)))
        return ResourceSizingResult(
            mode="published",
            mcs_index=m,
            packet_bytes=b,
            subchannel_size_prb=int(subchannel_size_prb),
            required_prb=prb,
            required_subchannels=sc,
            sinr_threshold_db=None if published_threshold_db is None else float(published_threshold_db),
            method="Todisco-2021 Table-3 anchor interpolation (190..350 B), conservative PRB ceiling",
        )
    raise ValueError("resource sizing mode must be fixed, legacy, or published")
