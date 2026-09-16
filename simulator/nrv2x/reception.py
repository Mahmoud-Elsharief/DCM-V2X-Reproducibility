from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import math
from pathlib import Path
from typing import Iterable, Sequence


# System-level minimum-SINR landmarks reported by Todisco et al.,
# "Performance Analysis of Sidelink 5G-V2X Mode 2 Through an Open-Source
# Simulator", Table 3.  They are Shannon-gap link abstractions, NOT 3GPP BLER
# curves.  Only the MCS values used by this project are frozen here.
_SYSTEM_LEVEL_SINR_DB = {
    # packet_bytes: {mcs: threshold dB}
    190: {4: 2.33, 11: 10.18},
    350: {4: 2.11, 11: 10.13},
}

# Legacy resource width is kept in resource_sizing.py.


def system_level_sinr_threshold_db(
    mcs_index: int | None,
    packet_bytes: int = 350,
    fallback_db: float | None = None,
) -> float:
    if mcs_index is not None:
        m = int(mcs_index)
        b = int(packet_bytes)
        table = _SYSTEM_LEVEL_SINR_DB.get(b, {})
        if m in table:
            return float(table[m])
        # The project paper uses 300 B.  Todisco et al. publish the same
        # system-level abstraction at 190 and 350 B. Interpolate only inside
        # that bracket; do not extrapolate and call it a universal PHY curve.
        lo, hi = 190, 350
        if lo <= b <= hi and m in _SYSTEM_LEVEL_SINR_DB[lo] and m in _SYSTEM_LEVEL_SINR_DB[hi]:
            y0 = float(_SYSTEM_LEVEL_SINR_DB[lo][m])
            y1 = float(_SYSTEM_LEVEL_SINR_DB[hi][m])
            return y0 + (b - lo) * (y1 - y0) / (hi - lo)
    if fallback_db is None:
        raise KeyError(
            f"No frozen threshold for MCS={mcs_index}, packet_bytes={packet_bytes}; "
            "provide an explicit fallback/calibration"
        )
    return float(fallback_db)


def legacy_required_subchannels(mcs_index: int | None, fallback: int = 1) -> int:
    # Compatibility wrapper for V0.1--V0.4 callers.
    from .resource_sizing import legacy_required_subchannels as _legacy
    return _legacy(mcs_index, fallback)


@dataclass(frozen=True)
class BLERCurve:
    """Smooth empirical BLER abstraction.

    This model is used only when an explicit link curve/sensitivity study is
    requested.  The default publication bridge uses the cited system-level
    minimum-SINR threshold rather than inventing a universal BLER waterfall.
    """

    midpoint_db: float
    transition_db: float = 1.0
    floor: float = 1e-5
    ceiling: float = 1.0 - 1e-5

    def bler(self, sinr_db: float) -> float:
        width = max(float(self.transition_db), 1e-6)
        x = (float(sinr_db) - float(self.midpoint_db)) / width
        if x >= 50:
            raw = 0.0
        elif x <= -50:
            raw = 1.0
        else:
            raw = 1.0 / (1.0 + math.exp(x))
        return min(self.ceiling, max(self.floor, raw))

    def success_probability(self, sinr_db: float) -> float:
        return 1.0 - self.bler(sinr_db)


@dataclass(frozen=True)
class BLERPoint:
    sinr_db: float
    bler: float
    weight: float = 1.0


def fit_logistic_bler(points: Sequence[BLERPoint]) -> BLERCurve:
    """Fit logit(BLER) with a straight line using weighted least squares.

    For BLER = 1/(1+exp((SINR-midpoint)/width)),
    log((1-BLER)/BLER) = SINR/width - midpoint/width.
    At least two non-degenerate points are required.
    """
    if len(points) < 2:
        raise ValueError("at least two BLER points are required")
    rows = []
    for p in points:
        b = min(1.0 - 1e-6, max(1e-6, float(p.bler)))
        w = max(0.0, float(p.weight))
        if w == 0:
            continue
        y = math.log((1.0 - b) / b)
        rows.append((float(p.sinr_db), y, w))
    if len(rows) < 2:
        raise ValueError("at least two positive-weight BLER points are required")
    sw = sum(w for _, _, w in rows)
    mx = sum(w * x for x, _, w in rows) / sw
    my = sum(w * y for _, y, w in rows) / sw
    varx = sum(w * (x - mx) ** 2 for x, _, w in rows)
    if varx <= 1e-15:
        raise ValueError("SINR points must not all be equal")
    cov = sum(w * (x - mx) * (y - my) for x, y, w in rows)
    slope = cov / varx
    if slope <= 0:
        raise ValueError("fitted BLER does not decrease with SINR")
    intercept = my - slope * mx
    width = 1.0 / slope
    midpoint = -intercept / slope
    return BLERCurve(midpoint_db=midpoint, transition_db=width)


def load_bler_points_csv(path: str | Path) -> list[BLERPoint]:
    """Load columns sinr_db,bler[,weight] for empirical calibration."""
    points: list[BLERPoint] = []
    with Path(path).open(newline="") as f:
        for row in csv.DictReader(f):
            points.append(BLERPoint(
                sinr_db=float(row["sinr_db"]),
                bler=float(row["bler"]),
                weight=float(row.get("weight") or 1.0),
            ))
    return points


@dataclass(frozen=True)
class SidelinkReceptionConfig:
    """Separate PSCCH and PSSCH system-level reception abstractions.

    ``*_mode='threshold'`` gives deterministic threshold decoding and is the
    default for the publication bridge. ``logistic`` is available for BLER
    sensitivity or empirical link-level calibration.
    """

    pscch_mode: str = "threshold"
    pscch_threshold_db: float = -3.0
    pscch_midpoint_db: float = -3.0
    pscch_transition_db: float = 0.8

    pssch_mode: str = "threshold"
    pssch_threshold_db: float = 10.13
    pssch_midpoint_db: float = 10.13
    pssch_transition_db: float = 1.0

    receiver_power_gate_dbm: float | None = -110.0
    seed: int = 1


class SidelinkReceptionModel:
    def __init__(self, cfg: SidelinkReceptionConfig):
        self.cfg = cfg
        for kind in (cfg.pscch_mode, cfg.pssch_mode):
            if kind not in ("threshold", "logistic"):
                raise ValueError("reception mode must be 'threshold' or 'logistic'")
        self.pscch_curve = BLERCurve(cfg.pscch_midpoint_db, cfg.pscch_transition_db)
        self.pssch_curve = BLERCurve(cfg.pssch_midpoint_db, cfg.pssch_transition_db)

    @staticmethod
    def _uniform01(*parts) -> float:
        payload = "|".join(str(x) for x in parts).encode("utf-8")
        digest = hashlib.blake2b(payload, digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        return (value + 0.5) / (2**64)

    def _draw_logistic(self, kind: str, sinr_db: float, *, sender_id, receiver_id,
                       packet_id, timestamp) -> tuple[bool, float, float]:
        curve = self.pscch_curve if kind == "pscch" else self.pssch_curve
        bler = curve.bler(sinr_db)
        u = self._uniform01(
            self.cfg.seed, kind, sender_id, receiver_id, packet_id,
            f"{float(timestamp):.9f}"
        )
        return u >= bler, bler, u

    @staticmethod
    def _threshold(sinr_db: float, threshold_db: float) -> tuple[bool, float, float]:
        ok = float(sinr_db) >= float(threshold_db)
        return ok, 0.0 if ok else 1.0, 1.0 if ok else 0.0

    def decode_pscch(self, sinr_db: float, **identity) -> tuple[bool, float, float]:
        if self.cfg.pscch_mode == "threshold":
            return self._threshold(sinr_db, self.cfg.pscch_threshold_db)
        return self._draw_logistic("pscch", sinr_db, **identity)

    def decode_pssch(self, sinr_db: float, desired_power_dbm: float | None = None,
                     **identity) -> tuple[bool, float, float]:
        if (self.cfg.receiver_power_gate_dbm is not None and
                desired_power_dbm is not None and
                float(desired_power_dbm) < float(self.cfg.receiver_power_gate_dbm)):
            return False, 1.0, 0.0
        if self.cfg.pssch_mode == "threshold":
            return self._threshold(sinr_db, self.cfg.pssch_threshold_db)
        return self._draw_logistic("pssch", sinr_db, **identity)


def default_pssch_midpoint_db(mcs_index: int | None, legacy_threshold_db: float) -> float:
    """Backward-compatible alias for V0.4 logistic sensitivity runs."""
    try:
        return system_level_sinr_threshold_db(mcs_index, 350, legacy_threshold_db)
    except KeyError:
        return float(legacy_threshold_db)
