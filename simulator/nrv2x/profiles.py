from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping, Sequence, Tuple


class NRProfileName(str, Enum):
    """Named reconstruction levels used in the SORA revision project."""

    LITE = "lite"
    CORE = "core"
    CORE_NO_REEVAL = "core_no_reeval"
    FULL = "full"


# TS 38.214 Table 8.1.4-1/2, Release 16.
_TPROC0_SLOTS = {0: 1, 1: 1, 2: 2, 3: 4}
_TPROC1_SLOTS = {0: 3, 1: 5, 2: 9, 3: 17}


def _validate_priority(priority: int) -> int:
    p = int(priority)
    if not 1 <= p <= 8:
        raise ValueError(f"sidelink priority must be in 1..8, got {priority!r}")
    return p


@dataclass(frozen=True)
class NRV2XProfile:
    """Configuration for a system-level Release-16 Mode-2 selector.

    The object deliberately separates the resource-selection procedure from
    propagation/reception.  It exposes the Release-16 timing constants and the
    priority-pair RSRP threshold structure rather than hiding them in simulator
    globals.

    A single-priority study may configure all 64 RSRP threshold entries to the
    same value; the lookup remains standards-shaped and can later accept a full
    8x8 table without changing protocol code.
    """

    name: NRProfileName
    numerology: int = 0
    bandwidth_mhz: int = 10
    subchannels_per_slot: int = 5
    required_subchannels: int = 1
    rri_s: float = 0.100

    # Resource-selection timing.
    sensing_window_s: float = 1.100
    # None means choose the maximum UE-implementation value Tproc,1.
    t1_s: float | None = None
    # Final end of selection window for the modeled PDB.  The RRC T2min list is
    # represented separately below; T2 must be >= T2min and <= remaining PDB.
    t2_s: float = 0.100
    packet_delay_budget_s: float = 0.100

    # RRC-style priority controls.  Priorities are 1..8 (1 highest).
    default_tx_priority: int = 1
    minimum_candidate_percent: float = 20.0
    # NR-V2X enforces the configured minimum candidate percentage. SORA may
    # disable this and rely on its cooperative resource map without +3 dB
    # relaxation when testing the map-only selection variant.
    enforce_minimum_candidate_percent: bool = True
    tx_percentage_by_priority: Mapping[int, float] | None = None
    # sl-SelectionWindowList values n1/n5/n10/n20 represented as base slot
    # counts.  At numerology mu, T2min is n * 2^mu slots, i.e. n ms.
    selection_t2min_base_slots_by_priority: Mapping[int, int] | None = None

    # Sensing thresholds.
    rsrp_threshold_dbm: float = -90.0
    rsrp_relax_step_db: float = 3.0
    maximum_relaxations: int = 20
    # Optional full 8x8 priority-pair table: (prio_RX_from_SCI, prio_TX_local).
    priority_rsrp_thresholds_dbm: Mapping[Tuple[int, int], float] | None = None
    sensing_reference: str = "pssch"  # "pssch" or "pscch"

    # Feature switches.
    use_time_history: bool = True
    use_sci_reservations: bool = True
    use_rsrp_exclusion: bool = True
    use_half_duplex_sensing: bool = True
    use_reevaluation: bool = True

    # Legacy bridge parameters.
    legacy_hold_s: float = 0.250

    # Physical-model tag.
    channel_model: str = "legacy_power_law"

    @property
    def slot_duration_s(self) -> float:
        return 0.001 / (2 ** self.numerology)

    @property
    def t_proc0_slots(self) -> int:
        try:
            return _TPROC0_SLOTS[int(self.numerology)]
        except KeyError as exc:
            raise ValueError("numerology must be one of 0,1,2,3") from exc

    @property
    def t_proc1_slots(self) -> int:
        try:
            return _TPROC1_SLOTS[int(self.numerology)]
        except KeyError as exc:
            raise ValueError("numerology must be one of 0,1,2,3") from exc

    @property
    def t_proc0_s(self) -> float:
        return self.t_proc0_slots * self.slot_duration_s

    @property
    def t_proc1_s(self) -> float:
        return self.t_proc1_slots * self.slot_duration_s

    @property
    def t3_s(self) -> float:
        # TS 38.214 8.1.4: T3 = Tproc,1.
        return self.t_proc1_s

    @property
    def effective_t1_s(self) -> float:
        if self.t1_s is None:
            return self.t_proc1_s
        value = float(self.t1_s)
        if value < 0 or value > self.t_proc1_s + 1e-12:
            raise ValueError(
                f"T1={value}s violates 0<=T1<=Tproc,1={self.t_proc1_s}s "
                f"for numerology {self.numerology}"
            )
        return value

    def candidate_percent_for(self, tx_priority: int | None = None) -> float:
        p = _validate_priority(self.default_tx_priority if tx_priority is None else tx_priority)
        if self.tx_percentage_by_priority and p in self.tx_percentage_by_priority:
            value = float(self.tx_percentage_by_priority[p])
        else:
            value = float(self.minimum_candidate_percent)
        if value not in (20.0, 35.0, 50.0):
            # Keep custom values possible for controlled sensitivity runs, but
            # fail loudly for accidental configuration errors.
            if not 0.0 < value <= 100.0:
                raise ValueError("candidate percentage must be in (0,100]")
        return value

    def t2min_s_for(self, tx_priority: int | None = None) -> float:
        p = _validate_priority(self.default_tx_priority if tx_priority is None else tx_priority)
        base = 20
        if self.selection_t2min_base_slots_by_priority:
            base = int(self.selection_t2min_base_slots_by_priority.get(p, base))
        if base not in (1, 5, 10, 20):
            raise ValueError("sl-SelectionWindow value must be one of n1,n5,n10,n20")
        # 38.331 defines n*2^mu slots; slot length is 1ms/2^mu, hence n ms.
        return base / 1000.0

    def effective_t2_s(self, tx_priority: int | None = None) -> float:
        requested = float(self.t2_s)
        if self.name is NRProfileName.LITE:
            return requested
        t2min = self.t2min_s_for(tx_priority)
        pdb = float(self.packet_delay_budget_s)
        if pdb <= 0:
            raise ValueError("packet_delay_budget_s must be >0")
        # TS 38.214: if T2min < PDB, choose T2 in [T2min,PDB], otherwise T2=PDB.
        if t2min < pdb:
            if requested < t2min - 1e-12 or requested > pdb + 1e-12:
                raise ValueError(f"T2={requested}s must be in [{t2min},{pdb}]s")
            return requested
        return pdb

    def threshold_for(self, received_priority: int, local_tx_priority: int) -> float:
        pi = _validate_priority(received_priority)
        pj = _validate_priority(local_tx_priority)
        if self.priority_rsrp_thresholds_dbm:
            value = self.priority_rsrp_thresholds_dbm.get((pi, pj))
            if value is not None:
                return float(value)
        return float(self.rsrp_threshold_dbm)

    def rsrp_threshold_list(self) -> tuple[float, ...]:
        """Return the 64 entries in TS 38.214/38.331 lookup order.

        Index i (1-based) is pi + (pj-1)*8, where pi is the priority in the
        decoded SCI and pj is the priority of the local transmission.
        """
        return tuple(
            self.threshold_for(pi, pj)
            for pj in range(1, 9)
            for pi in range(1, 9)
        )


def rrc_rsrp_code_to_dbm(code: int) -> float:
    """Decode SL-Thres-RSRP-r16 (38.331): 0=-inf, 66=+inf."""
    c = int(code)
    if not 0 <= c <= 66:
        raise ValueError("SL-Thres-RSRP code must be in 0..66")
    if c == 0:
        return float("-inf")
    if c == 66:
        return float("inf")
    return -128.0 + (c - 1) * 2.0


def rsrp_dbm_to_rrc_code(dbm: float) -> int:
    """Encode an exactly representable finite SL-Thres-RSRP-r16 value."""
    x = float(dbm)
    code_f = (x + 128.0) / 2.0 + 1.0
    code = round(code_f)
    if not 1 <= code <= 65 or abs(code_f - code) > 1e-9:
        raise ValueError("finite RSRP threshold must be an even 2-dB step from -128 to 0 dBm")
    return int(code)


def uniform_priority_thresholds(dbm: float) -> dict[Tuple[int, int], float]:
    return {(pi, pj): float(dbm) for pj in range(1, 9) for pi in range(1, 9)}


def profile(name: NRProfileName | str, **overrides) -> NRV2XProfile:
    name = NRProfileName(name)

    if name is NRProfileName.LITE:
        cfg = NRV2XProfile(
            name=name,
            sensing_window_s=0.250,
            t1_s=0.0,
            t2_s=0.100,
            use_time_history=False,
            use_sci_reservations=True,
            use_rsrp_exclusion=False,
            use_half_duplex_sensing=False,
            use_reevaluation=False,
            channel_model="legacy_power_law",
        )
    elif name is NRProfileName.CORE_NO_REEVAL:
        cfg = NRV2XProfile(name=name, use_reevaluation=False)
    elif name is NRProfileName.CORE:
        cfg = NRV2XProfile(name=name)
    elif name is NRProfileName.FULL:
        cfg = NRV2XProfile(name=name, channel_model="3gpp_v2v")
    else:  # pragma: no cover
        raise ValueError(name)

    if overrides:
        cfg = replace(cfg, **overrides)
    # Trigger key validation early.
    _ = cfg.effective_t1_s
    _ = cfg.effective_t2_s(cfg.default_tx_priority)
    if cfg.sensing_reference not in ("pssch", "pscch"):
        raise ValueError("sensing_reference must be 'pssch' or 'pscch'")
    return cfg
