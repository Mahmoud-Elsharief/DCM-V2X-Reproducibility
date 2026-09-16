from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
import random
from typing import Iterator, Sequence

from .profiles import NRV2XProfile


@dataclass(frozen=True, order=True)
class CandidateResource:
    """A single-slot sidelink candidate occupying contiguous subchannels."""

    abs_slot: int
    subchannels: tuple[int, ...]

    def overlaps(self, other: "CandidateResource") -> bool:
        if self.abs_slot != other.abs_slot:
            return False
        return not set(self.subchannels).isdisjoint(other.subchannels)


@dataclass(frozen=True)
class SensingRecord:
    """Decoded SCI plus the RSRP used for Release-16 sensing."""

    timestamp_s: float
    abs_slot: int
    subchannels: tuple[int, ...]
    rsrp_dbm: float
    tx_id: str | int | None = None
    # SCI format 1-A priority is 1..8; 1 is highest priority.
    tx_priority: int = 1
    reservation_period_s: float = 0.100
    sci_decoded: bool = True


@dataclass(frozen=True)
class CandidateSetResult:
    candidates: tuple[CandidateResource, ...]
    threshold_dbm: float
    relaxations: int
    total_candidates: int
    local_tx_priority: int = 1

    @property
    def candidate_percent(self) -> float:
        if self.total_candidates == 0:
            return 0.0
        return 100.0 * len(self.candidates) / self.total_candidates


@dataclass(frozen=True)
class ReevaluationResult:
    selected: CandidateResource
    changed: bool
    candidate_set: CandidateSetResult


class Mode2ResourceSelector:
    """System-level Release-16 NR sidelink Mode-2 candidate selector."""

    def __init__(self, cfg: NRV2XProfile, seed: int = 0):
        if cfg.required_subchannels < 1:
            raise ValueError("required_subchannels must be >=1")
        if cfg.required_subchannels > cfg.subchannels_per_slot:
            raise ValueError("required_subchannels exceeds pool width")
        if cfg.effective_t2_s(cfg.default_tx_priority) < cfg.effective_t1_s:
            raise ValueError("T2 must be >= T1")
        self.cfg = cfg
        self.rng = random.Random(seed)
        self._history: list[SensingRecord] = []
        self._own_tx_slots: set[int] = set()

    @property
    def slot_duration_s(self) -> float:
        return self.cfg.slot_duration_s

    def time_to_slot_floor(self, t_s: float) -> int:
        return floor((t_s + 1e-12) / self.slot_duration_s)

    def time_to_slot_ceil(self, t_s: float) -> int:
        return ceil((t_s - 1e-12) / self.slot_duration_s)

    # ---------- sensing database ----------
    def mark_own_tx(self, abs_slot: int) -> None:
        self._own_tx_slots.add(int(abs_slot))

    def observe_sci(self, record: SensingRecord) -> bool:
        if not record.sci_decoded:
            return False
        if not 1 <= int(record.tx_priority) <= 8:
            raise ValueError("received SCI priority must be in 1..8")
        if self.cfg.use_half_duplex_sensing and record.abs_slot in self._own_tx_slots:
            return False
        self._history.append(record)
        return True

    def purge(self, now_s: float) -> None:
        horizon = self.cfg.sensing_window_s if self.cfg.use_time_history else self.cfg.legacy_hold_s
        start = now_s - horizon
        self._history = [r for r in self._history if r.timestamp_s >= start]
        oldest_slot = self.time_to_slot_floor(start) - 1
        self._own_tx_slots = {s for s in self._own_tx_slots if s >= oldest_slot}

    def _valid_sensing_records(self, now_s: float) -> tuple[SensingRecord, ...]:
        """Records in [n-T0, n-Tproc,0), excluding the processing gap."""
        if not self.cfg.use_time_history:
            start = now_s - self.cfg.legacy_hold_s
            return tuple(r for r in self._history if start <= r.timestamp_s <= now_s)
        start = now_s - self.cfg.sensing_window_s
        cutoff = now_s - self.cfg.t_proc0_s
        return tuple(r for r in self._history if start <= r.timestamp_s < cutoff - 1e-15)

    @property
    def sensing_history(self) -> tuple[SensingRecord, ...]:
        return tuple(self._history)

    # ---------- candidate generation ----------
    def contiguous_blocks(self) -> tuple[tuple[int, ...], ...]:
        width = self.cfg.required_subchannels
        return tuple(
            tuple(range(start, start + width))
            for start in range(self.cfg.subchannels_per_slot - width + 1)
        )

    def all_candidates(self, now_s: float, tx_priority: int | None = None) -> tuple[CandidateResource, ...]:
        first = self.time_to_slot_ceil(now_s + self.cfg.effective_t1_s)
        last = self.time_to_slot_floor(now_s + self.cfg.effective_t2_s(tx_priority))
        if last < first:
            return ()
        blocks = self.contiguous_blocks()
        return tuple(CandidateResource(slot, block) for slot in range(first, last + 1) for block in blocks)

    def _project_record_into_window(
        self,
        record: SensingRecord,
        first_slot: int,
        last_slot: int,
    ) -> Iterator[CandidateResource]:
        period_slots = max(1, round(record.reservation_period_s / self.slot_duration_s))

        if not self.cfg.use_sci_reservations:
            if first_slot <= record.abs_slot <= last_slot:
                yield CandidateResource(record.abs_slot, record.subchannels)
            return

        if record.abs_slot >= first_slot:
            k = 0
        else:
            k = ceil((first_slot - record.abs_slot) / period_slots)
        slot = record.abs_slot + k * period_slots
        while slot <= last_slot:
            yield CandidateResource(slot, record.subchannels)
            slot += period_slots

    def _excluded_candidates(
        self,
        now_s: float,
        candidates: Sequence[CandidateResource],
        threshold_offset_db: float,
        local_tx_priority: int,
    ) -> set[CandidateResource]:
        if not candidates:
            return set()
        first_slot = min(c.abs_slot for c in candidates)
        last_slot = max(c.abs_slot for c in candidates)
        excluded: set[CandidateResource] = set()

        for rec in self._valid_sensing_records(now_s):
            threshold = self.cfg.threshold_for(rec.tx_priority, local_tx_priority) + threshold_offset_db
            if self.cfg.use_rsrp_exclusion and rec.rsrp_dbm < threshold:
                continue
            for projected in self._project_record_into_window(rec, first_slot, last_slot):
                for cand in candidates:
                    if cand.overlaps(projected):
                        excluded.add(cand)
        return excluded

    def candidate_set(self, now_s: float, tx_priority: int | None = None) -> CandidateSetResult:
        local_priority = self.cfg.default_tx_priority if tx_priority is None else int(tx_priority)
        # Validate via profile lookup.
        target = self.cfg.candidate_percent_for(local_priority)
        self.purge(now_s)
        all_candidates = self.all_candidates(now_s, local_priority)
        if not all_candidates:
            return CandidateSetResult((), self.cfg.rsrp_threshold_dbm, 0, 0, local_priority)

        relaxations = 0
        while True:
            offset = relaxations * self.cfg.rsrp_relax_step_db
            excluded = self._excluded_candidates(now_s, all_candidates, offset, local_priority)
            remaining = tuple(c for c in all_candidates if c not in excluded)
            pct = 100.0 * len(remaining) / len(all_candidates)
            # SORA map-only mode intentionally skips the NR-V2X 20/35/50%
            # candidate-floor procedure. It performs one sensing exclusion pass
            # at the configured threshold and then relies on the cooperative RS
            # map. NR-V2X always keeps this flag enabled.
            if (not self.cfg.enforce_minimum_candidate_percent) or pct >= target or relaxations >= self.cfg.maximum_relaxations:
                effective = self.cfg.rsrp_threshold_dbm + offset
                return CandidateSetResult(
                    remaining, effective, relaxations, len(all_candidates), local_priority
                )
            relaxations += 1

    def select(self, now_s: float, tx_priority: int | None = None) -> CandidateResource:
        result = self.candidate_set(now_s, tx_priority=tx_priority)
        if not result.candidates:
            raise RuntimeError("No Mode-2 candidate resource is available")
        return self.rng.choice(result.candidates)

    def reevaluate(
        self,
        selected: CandidateResource,
        now_s: float,
        tx_priority: int | None = None,
    ) -> ReevaluationResult:
        """Re-evaluate a pre-selected resource using the newest valid sensing DB.

        Scheduling *when* this call occurs is a MAC/integration responsibility.
        The integration uses T3=Tproc,1 before first SCI indication/first use.
        """
        local_priority = self.cfg.default_tx_priority if tx_priority is None else int(tx_priority)
        result = self.candidate_set(now_s, tx_priority=local_priority)
        if not self.cfg.use_reevaluation:
            return ReevaluationResult(selected, False, result)

        offset = result.relaxations * self.cfg.rsrp_relax_step_db
        excluded = self._excluded_candidates(now_s, (selected,), offset, local_priority)
        if selected not in excluded:
            return ReevaluationResult(selected, False, result)
        if not result.candidates:
            raise RuntimeError("Selected resource is no longer valid and no replacement exists")
        replacement = self.rng.choice(result.candidates)
        return ReevaluationResult(replacement, replacement != selected, result)
