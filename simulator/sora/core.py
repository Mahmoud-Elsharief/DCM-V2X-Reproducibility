from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import random
from typing import Iterable, Mapping, Sequence

from nrv2x.mode2 import CandidateResource, CandidateSetResult, Mode2ResourceSelector


def future_resources_on_air(
    future_resources: Sequence[tuple[int, int]],
    *,
    enabled: bool = True,
    advertise: bool = True,
) -> tuple[tuple[int, int], ...]:
    """Return the SORA future resources carried on air.

    Main-paper SORA advertises the prepared future resource to direct one-hop
    neighbors. The advertisement is not propagated through the ordinary
    two-hop resource-status map.
    """
    if not enabled or not advertise:
        return ()
    return tuple(tuple(x) for x in future_resources)


class ResourceState(IntEnum):
    """SORA resource-state codes used by the reviewer-validation variant.

    FREE/ONE_HOP/TWO_HOP retain the submitted protocol semantics.  The fourth
    2-bit code, previously unused in the manuscript, is used optionally as a
    direct-neighbor tentative future reservation.
    """

    FREE = 0
    ONE_HOP = 1
    TWO_HOP = 2
    TENTATIVE_FUTURE = 3


@dataclass
class ResourceKnowledge:
    state: ResourceState = ResourceState.FREE
    updated_s: float = float("-inf")
    distance_m: float = 0.0
    source_id: str | int | None = None


@dataclass(frozen=True)
class SORAReport:
    timestamp_s: float
    sender_id: str | int
    current_resources: tuple[tuple[int, int], ...]
    future_resources: tuple[tuple[int, int], ...]
    resource_status: Mapping[tuple[int, int], int]
    distance_m: float
    rs_valid: bool = True


@dataclass(frozen=True)
class SORACandidateResult:
    selected: CandidateResource
    nr_candidates: CandidateSetResult
    cooperative_candidates: tuple[CandidateResource, ...]
    used_fallback: bool = False
    map_relaxations: int = 0
    cooperative_candidate_percent: float = 0.0
    map_threshold_dbm: float | None = None


@dataclass(frozen=True)
class CollisionFeedback:
    reports: int
    acknowledgement_ratio: float | None
    collision_suspected: bool
    strong_reports: int = 0
    strong_acknowledgement_ratio: float | None = None


class SORAEngine:
    """Clean SORA overlay on top of the reconstructed Release-16 Mode-2 core.

    The engine intentionally contains only the SORA-specific mechanisms:
      * effective 0/1/2 cooperative two-hop map;
      * full-state or hybrid full/delta cooperative RS exchange;
      * preselected future resource advertised to direct one-hop neighbors and continuously verified;
      * RS acknowledgement ratio used as implicit collision feedback.

    Local sensing, candidate-window construction, SCI reservations, RSRP
    exclusion, +3 dB relaxation, re-evaluation timing, resource sizing and the
    channel/reception model stay in the common NR-V2X foundation.
    """

    def __init__(
        self,
        selector: Mode2ResourceSelector,
        *,
        rri_s: float,
        slots_per_rri: int,
        subchannels_per_slot: int,
        seed: int = 0,
        collision_threshold: float = 0.8,
        map_expiry_rri: float = 2.0,
        minimum_feedback_reports: int = 1,
        collision_mode: str = "legacy_single",
        strong_threshold: float = 0.9,
        strong_neighbor_fraction: float = 0.5,
        enable_two_hop: bool = True,
        reserve_announced_future: bool = False,
        enable_map_candidate_floor: bool = False,
        map_candidate_floor_percent: float = 5.0,
        map_relax_step_db: float = 3.0,
        map_max_relaxations: int = 20,
        map_initial_threshold_dbm: float = -90.0,
        map_distance_from_threshold=None,
    ):
        self.selector = selector
        self.rri_s = float(rri_s)
        self.slots_per_rri = int(slots_per_rri)
        self.subchannels_per_slot = int(subchannels_per_slot)
        self.collision_threshold = float(collision_threshold)
        self.map_expiry_s = float(map_expiry_rri) * self.rri_s
        self.minimum_feedback_reports = max(1, int(minimum_feedback_reports))
        if collision_mode not in ("legacy_single", "paper_dual"):
            raise ValueError("collision_mode must be legacy_single or paper_dual")
        self.collision_mode = collision_mode
        self.strong_threshold = float(strong_threshold)
        self.strong_neighbor_fraction = float(strong_neighbor_fraction)
        if not 0 < self.strong_neighbor_fraction <= 1:
            raise ValueError("strong_neighbor_fraction must be in (0,1]")
        self.enable_two_hop = bool(enable_two_hop)
        self.reserve_announced_future = bool(reserve_announced_future)
        self.enable_map_candidate_floor = bool(enable_map_candidate_floor)
        self.map_candidate_floor_percent = float(map_candidate_floor_percent)
        if not 0.0 <= self.map_candidate_floor_percent <= 100.0:
            raise ValueError("map_candidate_floor_percent must be in [0,100]")
        self.map_relax_step_db = float(map_relax_step_db)
        self.map_max_relaxations = max(0, int(map_max_relaxations))
        self.map_initial_threshold_dbm = float(map_initial_threshold_dbm)
        self.map_distance_from_threshold = map_distance_from_threshold
        self.rng = random.Random(seed)

        self._map: dict[tuple[int, int], ResourceKnowledge] = {
            (slot, ch): ResourceKnowledge()
            for slot in range(self.slots_per_rri)
            for ch in range(self.subchannels_per_slot)
        }
        self._reports_since_tx: list[SORAReport] = []
        self._last_tx_resources: tuple[tuple[int, int], ...] = ()
        # Advertised future resources are intentionally kept OUTSIDE the
        # cooperative 0/1/2 RS map.  They are learned only from packets decoded
        # directly from the advertising sender and are never relayed as two-hop
        # occupancy.  This cleanly isolates the one-hop future-advertisement
        # experiment from the existing SORA map/codec.
        self._direct_future_by_sender: dict[
            str | int,
            tuple[tuple[tuple[int, int], ...], float, float],
        ] = {}
        # Retained only for backward-compatible diagnostics; no future state is
        # inserted into the on-air RS map.
        self._known_future_by_sender: dict[str | int, tuple[tuple[int, int], ...]] = {}
        # Purging a 500-entry map for every candidate check is redundant when
        # many checks share the same simulator timestamp. Cache the last purge
        # time; updates at that same timestamp cannot already be expired.
        self._last_purge_s: float | None = None

    # ------------------------------------------------------------------
    # Resource-space conversion helpers
    # ------------------------------------------------------------------
    def phase_key(self, abs_slot: int, subchannel: int) -> tuple[int, int]:
        return (int(abs_slot) % self.slots_per_rri, int(subchannel))

    def candidate_phase_resources(self, candidate: CandidateResource) -> tuple[tuple[int, int], ...]:
        return tuple(self.phase_key(candidate.abs_slot, ch) for ch in candidate.subchannels)

    # ------------------------------------------------------------------
    # Cooperative RS map
    # ------------------------------------------------------------------
    def purge(self, now_s: float) -> None:
        now_s = float(now_s)
        if self._last_purge_s is not None and abs(now_s - self._last_purge_s) <= 1e-12:
            return
        self._last_purge_s = now_s
        for key, knowledge in self._map.items():
            if knowledge.state is ResourceState.FREE:
                continue
            if now_s - knowledge.updated_s > self.map_expiry_s:
                self._map[key] = ResourceKnowledge()

        # Direct future advertisements use the same freshness horizon as the
        # cooperative map but remain a separate one-hop-only data structure.
        stale_future = [
            sender_id
            for sender_id, (_, updated_s, _) in self._direct_future_by_sender.items()
            if now_s - float(updated_s) > self.map_expiry_s
        ]
        for sender_id in stale_future:
            self._direct_future_by_sender.pop(sender_id, None)

    def _set_state(
        self,
        resource: tuple[int, int],
        state: ResourceState,
        now_s: float,
        distance_m: float,
        source_id: str | int | None,
        *,
        direct_wins: bool = True,
    ) -> None:
        if resource not in self._map:
            return
        current = self._map[resource]
        if (direct_wins and current.state is ResourceState.ONE_HOP and
                state in (ResourceState.TWO_HOP, ResourceState.TENTATIVE_FUTURE)):
            return
        self._map[resource] = ResourceKnowledge(state, now_s, float(distance_m), source_id)

    def ingest_report(self, report: SORAReport) -> None:
        """Apply the protocol-3 propagation rule to a decoded SORA report.

        Direct current resources are ONE_HOP. A sender's ONE_HOP entries become
        TWO_HOP at the receiver. Sender TWO_HOP information is deliberately not
        propagated to a third hop.  By default the advertised future resource
        remains free, preserving the submitted behavior; the reviewer-validation
        variant can mark it TENTATIVE_FUTURE for direct neighbors.
        """
        self.purge(report.timestamp_s)
        # Only a receiver-side reconstructed/valid RS may be used as
        # collision feedback. Direct current-resource information remains
        # useful even when a delta chain is invalid.
        if report.rs_valid:
            self._reports_since_tx.append(report)
        self._known_future_by_sender[report.sender_id] = tuple(report.future_resources)
        if self.reserve_announced_future:
            # This report was decoded directly from ``report.sender_id``.
            # Store only that sender's advertised future resource locally.
            # It is deliberately NOT copied into ``self._map`` and therefore
            # cannot be serialized/forwarded as a two-hop RS state.
            if report.future_resources:
                self._direct_future_by_sender[report.sender_id] = (
                    tuple(tuple(x) for x in report.future_resources),
                    float(report.timestamp_s),
                    float(report.distance_m),
                )
            else:
                self._direct_future_by_sender.pop(report.sender_id, None)

        # First propagate the sender's map by exactly one additional hop.
        # An invalid/missed-delta chain is never used as two-hop evidence.
        if report.rs_valid:
            for resource, raw_state in report.resource_status.items():
                try:
                    state = ResourceState(int(raw_state))
                except (TypeError, ValueError):
                    continue
                if state is ResourceState.ONE_HOP and self.enable_two_hop:
                    self._set_state(
                        tuple(resource), ResourceState.TWO_HOP,
                        report.timestamp_s, report.distance_m, report.sender_id,
                    )
                # TWO_HOP and FREE are not propagated as occupancy.

        # Directly decoded current resources have precedence over relayed state.
        for resource in report.current_resources:
            self._set_state(
                tuple(resource), ResourceState.ONE_HOP,
                report.timestamp_s, report.distance_m, report.sender_id,
                direct_wins=False,
            )

        # Advertised future resources are handled only through
        # ``_direct_future_by_sender`` above.  No TENTATIVE_FUTURE entry is
        # inserted into the ordinary RS map, so the one-hop experiment cannot
        # leak into two-hop propagation or the 0/1/2 Golomb-Rice codec.

    def mark_own_current(self, resources: Sequence[tuple[int, int]], now_s: float) -> None:
        for resource in resources:
            self._set_state(
                tuple(resource), ResourceState.ONE_HOP,
                now_s, 0.0, "self", direct_wins=False,
            )

    def clear_own_current(self, resources: Sequence[tuple[int, int]]) -> None:
        for resource in resources:
            key = tuple(resource)
            if key in self._map and self._map[key].source_id == "self":
                self._map[key] = ResourceKnowledge()

    def snapshot(self, now_s: float, own_current: Sequence[tuple[int, int]] = (), *, sparse: bool = True, max_entries: int | None = None) -> dict[tuple[int, int], int]:
        """Return the effective RS vector.

        ``max_entries`` enables a fixed-budget revision variant.  Direct
        one-hop entries are retained first, followed by tentative future
        reservations and then two-hop entries; within each class, the most
        recently updated/closest information is preferred.  This preserves
        collision feedback while bounding the on-air control payload.
        """
        self.purge(now_s)
        if sparse:
            items = [(key, value) for key, value in self._map.items() if value.state is not ResourceState.FREE]
            for resource in own_current:
                key = tuple(resource)
                if key in self._map:
                    items = [(k, v) for k, v in items if k != key]
                    items.append((key, ResourceKnowledge(ResourceState.ONE_HOP, now_s, 0.0, "self")))
            if max_entries is not None and int(max_entries) > 0 and len(items) > int(max_entries):
                pri = {ResourceState.ONE_HOP: 0, ResourceState.TENTATIVE_FUTURE: 1, ResourceState.TWO_HOP: 2}
                items.sort(key=lambda kv: (pri.get(kv[1].state, 9), -float(kv[1].updated_s), float(kv[1].distance_m)))
                items = items[:int(max_entries)]
            return {key: int(value.state) for key, value in items}
        result = {key: int(value.state) for key, value in self._map.items()}
        for resource in own_current:
            key = tuple(resource)
            if key in self._map:
                result[key] = int(ResourceState.ONE_HOP)
        return result

    def state(self, resource: tuple[int, int], now_s: float | None = None) -> ResourceState:
        if now_s is not None:
            self.purge(now_s)
        return self._map.get(tuple(resource), ResourceKnowledge()).state

    def _cooperative_percent(self, candidates: Sequence[CandidateResource], nr_set: CandidateSetResult) -> float:
        if nr_set.total_candidates <= 0:
            return 0.0
        return 100.0 * len(candidates) / nr_set.total_candidates

    def _release_map_by_distance(self, distance_cutoff_m: float) -> tuple[int, int]:
        """Apply one legacy SORA/CRA6G map-relaxation step.

        Distant TWO_HOP restrictions are released first.  Only when that step
        releases no TWO_HOP entries do we release distant ONE_HOP entries.
        This mirrors ``check_and_adjust_neighbors_CRA6G`` while keeping the
        reconstructed 0/1/2 map.
        """
        two_hop_released = 0
        one_hop_released = 0
        for key, knowledge in list(self._map.items()):
            if (knowledge.state is ResourceState.TWO_HOP and
                    knowledge.distance_m > float(distance_cutoff_m)):
                self._map[key] = ResourceKnowledge()
                two_hop_released += 1
        if two_hop_released == 0:
            for key, knowledge in list(self._map.items()):
                if (knowledge.state is ResourceState.ONE_HOP and
                        knowledge.source_id != "self" and
                        knowledge.distance_m > float(distance_cutoff_m)):
                    self._map[key] = ResourceKnowledge()
                    one_hop_released += 1
        return two_hop_released, one_hop_released

    def _apply_map_candidate_floor(
        self,
        now_s: float,
        nr_set: CandidateSetResult,
        *,
        exclude_phase: Iterable[tuple[int, int]] = (),
    ) -> tuple[tuple[CandidateResource, ...], int, float, float]:
        """Ensure at least the SORA-specific map-safe candidate floor.

        The floor is evaluated *after* the cooperative RS map.  If fewer than
        the configured percentage remain, the map sensing threshold is relaxed
        by +3 dB per step (configurable), which reduces the corresponding
        distance cutoff.  Distant two-hop entries are released before one-hop
        entries, matching the historical SORA/CRA6G behavior.
        """
        excluded_phase = set(tuple(x) for x in exclude_phase)

        def current_candidates():
            out = []
            for cand in nr_set.candidates:
                phase = set(self.candidate_phase_resources(cand))
                if excluded_phase and phase == excluded_phase:
                    continue
                if self.candidate_is_cooperatively_free(cand, now_s):
                    out.append(cand)
            return tuple(out)

        coop = current_candidates()
        pct = self._cooperative_percent(coop, nr_set)
        threshold = self.map_initial_threshold_dbm
        if (not self.enable_map_candidate_floor or
                pct >= self.map_candidate_floor_percent or
                self.map_candidate_floor_percent <= 0.0):
            return coop, 0, pct, threshold

        if self.map_distance_from_threshold is None:
            # No geometry conversion available: leave the map untouched rather
            # than silently applying a physically undefined relaxation.
            return coop, 0, pct, threshold

        relaxations = 0
        while pct < self.map_candidate_floor_percent and relaxations < self.map_max_relaxations:
            relaxations += 1
            threshold = self.map_initial_threshold_dbm + relaxations * self.map_relax_step_db
            cutoff_m = float(self.map_distance_from_threshold(threshold))
            self._release_map_by_distance(cutoff_m)
            coop = current_candidates()
            pct = self._cooperative_percent(coop, nr_set)
        return coop, relaxations, pct, threshold

    # ------------------------------------------------------------------
    # Candidate selection / future-resource verification
    # ------------------------------------------------------------------
    def candidate_is_cooperatively_free(self, candidate: CandidateResource, now_s: float) -> bool:
        self.purge(now_s)
        return all(
            self.state(self.phase_key(candidate.abs_slot, ch)) is ResourceState.FREE
            for ch in candidate.subchannels
        )

    def cooperative_candidate_set(
        self,
        now_s: float,
        *,
        tx_priority: int | None = None,
        exclude_phase: Iterable[tuple[int, int]] = (),
    ) -> tuple[CandidateSetResult, tuple[CandidateResource, ...]]:
        nr_set = self.selector.candidate_set(now_s, tx_priority=tx_priority)
        coop, _, _, _ = self._apply_map_candidate_floor(
            now_s, nr_set, exclude_phase=exclude_phase
        )
        return nr_set, coop

    def select(
        self,
        now_s: float,
        *,
        tx_priority: int | None = None,
        exclude_phase: Iterable[tuple[int, int]] = (),
        allow_nr_fallback: bool = True,
        stream: str = "common",
    ) -> SORACandidateResult:
        nr_set = self.selector.candidate_set(now_s, tx_priority=tx_priority)
        coop, map_relaxations, coop_pct, map_threshold = self._apply_map_candidate_floor(
            now_s, nr_set, exclude_phase=exclude_phase
        )
        rng = self.selector.rng if stream == "common" else self.rng
        if coop:
            return SORACandidateResult(
                rng.choice(coop), nr_set, coop, False, map_relaxations,
                coop_pct, map_threshold
            )
        if not allow_nr_fallback or not nr_set.candidates:
            raise RuntimeError("No SORA cooperative candidate resource is available")
        excluded_phase = set(tuple(x) for x in exclude_phase)
        fallback = tuple(
            c for c in nr_set.candidates
            if not excluded_phase or set(self.candidate_phase_resources(c)) != excluded_phase
        ) or nr_set.candidates
        return SORACandidateResult(
            rng.choice(fallback), nr_set, (), True, map_relaxations,
            coop_pct, map_threshold
        )

    def direct_future_resources(self, now_s: float) -> set[tuple[int, int]]:
        """Return fresh future resources advertised by DIRECT neighbors only."""
        self.purge(now_s)
        blocked: set[tuple[int, int]] = set()
        if not self.reserve_announced_future:
            return blocked
        for resources, _, _ in self._direct_future_by_sender.values():
            blocked.update(tuple(r) for r in resources)
        return blocked

    def candidate_conflicts_direct_future(
        self, candidate: CandidateResource, now_s: float
    ) -> bool:
        blocked = self.direct_future_resources(now_s)
        if not blocked:
            return False
        return any(
            self.phase_key(candidate.abs_slot, ch) in blocked
            for ch in candidate.subchannels
        )

    def select_future(
        self,
        now_s: float,
        *,
        tx_priority: int | None = None,
        exclude_phase: Iterable[tuple[int, int]] = (),
        allow_nr_fallback: bool = True,
        stream: str = "sora",
    ) -> SORACandidateResult:
        """Select SORA's prepared future resource.

        Advertisement OFF:
            This is behaviorally identical to ``select``.
        Advertisement ON:
            First construct the exact same ordinary SORA candidate pool, then
            prefer candidates that do not overlap a future resource advertised
            by a directly decoded neighbor.  If every otherwise-valid
            candidate conflicts, fall back to the original pool without any
            Smart/closest-neighbor ranking.
        """
        nr_set = self.selector.candidate_set(now_s, tx_priority=tx_priority)
        coop, map_relaxations, coop_pct, map_threshold = self._apply_map_candidate_floor(
            now_s, nr_set, exclude_phase=exclude_phase
        )
        rng = self.selector.rng if stream == "common" else self.rng

        used_fallback = False
        if coop:
            base_pool = tuple(coop)
        else:
            if not allow_nr_fallback or not nr_set.candidates:
                raise RuntimeError("No SORA cooperative candidate resource is available")
            excluded_phase = set(tuple(x) for x in exclude_phase)
            base_pool = tuple(
                c for c in nr_set.candidates
                if not excluded_phase or set(self.candidate_phase_resources(c)) != excluded_phase
            ) or tuple(nr_set.candidates)
            used_fallback = True

        choice_pool = base_pool
        if self.reserve_announced_future:
            clean = tuple(
                c for c in base_pool
                if not self.candidate_conflicts_direct_future(c, now_s)
            )
            if clean:
                choice_pool = clean

        return SORACandidateResult(
            rng.choice(choice_pool), nr_set, coop, used_fallback,
            map_relaxations, coop_pct, map_threshold
        )

    def future_is_available(
        self,
        future_resources: Sequence[tuple[int, int]],
        now_s: float,
        *,
        tx_priority: int | None = None,
        exclude_phase: Iterable[tuple[int, int]] = (),
    ) -> bool:
        """Verify the prepared future resource.

        Ordinary SORA map availability is always required.  When one-hop
        future advertisement is enabled, a direct-future conflict triggers a
        reselection only if at least one clean ordinary SORA candidate exists.
        If all otherwise-valid candidates are future-conflicting, the current
        choice is held; this avoids artificial reselection churn and adds no
        Smart ranking.
        """
        self.purge(now_s)
        if not future_resources:
            return False
        if not all(
            self.state(tuple(resource)) is ResourceState.FREE
            for resource in future_resources
        ):
            return False

        if not self.reserve_announced_future:
            return True

        blocked = self.direct_future_resources(now_s)
        if not blocked or not any(tuple(r) in blocked for r in future_resources):
            return True

        _, coop = self.cooperative_candidate_set(
            now_s, tx_priority=tx_priority, exclude_phase=exclude_phase
        )
        if coop:
            return not any(
                not self.candidate_conflicts_direct_future(c, now_s)
                for c in coop
            )

        # If there is no cooperative alternative, preserve the existing
        # fallback behavior rather than repeatedly churning future resources.
        return True

    # ------------------------------------------------------------------
    # Collision inference from returned RS maps
    # ------------------------------------------------------------------
    def start_feedback_window(self, current_resources: Sequence[tuple[int, int]]) -> None:
        self._last_tx_resources = tuple(tuple(x) for x in current_resources)
        self._reports_since_tx.clear()

    def _ack_ratio(self, reports: Sequence[SORAReport]) -> float | None:
        if not reports or not self._last_tx_resources:
            return None
        ratios: list[float] = []
        for resource in self._last_tx_resources:
            eligible = [r for r in reports if tuple(resource) in r.resource_status]
            if not eligible:
                # Missing from a sparse/delta report means "not carried", not
                # FREE. Do not manufacture a negative acknowledgement.
                return None
            ack = sum(
                1 for report in eligible
                if int(report.resource_status[tuple(resource)]) == int(ResourceState.ONE_HOP)
            )
            ratios.append(ack / len(eligible))
        return min(ratios) if ratios else None

    def evaluate_collision_feedback(self) -> CollisionFeedback:
        reports = len(self._reports_since_tx)
        if reports < self.minimum_feedback_reports or not self._last_tx_resources:
            return CollisionFeedback(reports, None, False)

        ratio = self._ack_ratio(self._reports_since_tx)
        if ratio is None:
            return CollisionFeedback(reports, None, False)
        if self.collision_mode == "legacy_single":
            return CollisionFeedback(reports, ratio, ratio <= self.collision_threshold)

        # Paper dual-threshold option: choose an explicit closest-neighbor
        # subset because the manuscript defines J^st by proximity/strong link
        # but does not specify its cardinality.
        ordered = sorted(self._reports_since_tx, key=lambda r: r.distance_m)
        k = max(1, int(round(len(ordered) * self.strong_neighbor_fraction)))
        strong = ordered[:k]
        strong_ratio = self._ack_ratio(strong)
        if strong_ratio is None:
            return CollisionFeedback(reports, ratio, False, strong_reports=len(strong), strong_acknowledgement_ratio=None)
        collision = (ratio < self.collision_threshold) and (strong_ratio < self.strong_threshold)
        return CollisionFeedback(
            reports, ratio, collision,
            strong_reports=len(strong),
            strong_acknowledgement_ratio=strong_ratio,
        )

    @property
    def reports_since_tx(self) -> tuple[SORAReport, ...]:
        return tuple(self._reports_since_tx)

    @property
    def known_future_by_sender(self) -> Mapping[str | int, tuple[tuple[int, int], ...]]:
        return dict(self._known_future_by_sender)
