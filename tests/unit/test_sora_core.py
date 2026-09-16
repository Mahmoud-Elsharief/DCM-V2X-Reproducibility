from nrv2x import Mode2ResourceSelector, profile
from sora import ResourceState, SORAEngine, SORAReport


def make_engine(seed=1):
    cfg = profile(
        "core",
        numerology=0,
        bandwidth_mhz=10,
        subchannels_per_slot=5,
        required_subchannels=1,
        rri_s=0.1,
        t1_s=0.001,
        t2_s=0.020,
        packet_delay_budget_s=0.1,
        minimum_candidate_percent=20.0,
    )
    selector = Mode2ResourceSelector(cfg, seed=seed)
    return SORAEngine(
        selector, rri_s=0.1, slots_per_rri=100,
        subchannels_per_slot=5, seed=seed,
        collision_threshold=0.8, map_expiry_rri=2.0,
    )


def report(*, t=0.0, sender=2, current=(), future=(), rs=None, distance=20.0):
    return SORAReport(
        timestamp_s=t,
        sender_id=sender,
        current_resources=tuple(current),
        future_resources=tuple(future),
        resource_status={} if rs is None else rs,
        distance_m=distance,
    )


def test_direct_current_becomes_one_hop():
    e = make_engine()
    e.ingest_report(report(current=((3, 1),)))
    assert e.state((3, 1)) is ResourceState.ONE_HOP


def test_one_hop_from_neighbor_becomes_two_hop():
    e = make_engine()
    e.ingest_report(report(rs={(4, 2): 1}))
    assert e.state((4, 2)) is ResourceState.TWO_HOP


def test_two_hop_does_not_propagate_to_third_hop():
    e = make_engine()
    e.ingest_report(report(rs={(4, 2): 2}))
    assert e.state((4, 2)) is ResourceState.FREE


def test_direct_current_overrides_relayed_two_hop():
    e = make_engine()
    e.ingest_report(report(current=((4, 2),), rs={(4, 2): 1}))
    assert e.state((4, 2)) is ResourceState.ONE_HOP


def test_future_advertisement_is_not_an_exclusion_when_h_equals_l():
    e = make_engine()
    e.ingest_report(report(future=((7, 3),)))
    assert e.state((7, 3)) is ResourceState.FREE
    assert e.known_future_by_sender[2] == ((7, 3),)


def test_rs_entries_expire_after_two_rri():
    e = make_engine()
    e.ingest_report(report(t=0.0, current=((2, 0),)))
    assert e.state((2, 0), 0.19) is ResourceState.ONE_HOP
    assert e.state((2, 0), 0.201) is ResourceState.FREE


def test_cooperative_occupied_candidate_is_filtered():
    e = make_engine()
    nr_set = e.selector.candidate_set(0.0)
    chosen = nr_set.candidates[0]
    phase = e.candidate_phase_resources(chosen)[0]
    e.ingest_report(report(current=(phase,)))
    _, coop = e.cooperative_candidate_set(0.0)
    assert chosen not in coop


def test_future_verification_uses_cooperative_map():
    e = make_engine()
    future = ((9, 1),)
    assert e.future_is_available(future, 0.0)
    e.ingest_report(report(current=future))
    assert not e.future_is_available(future, 0.0)


def test_feedback_below_point8_triggers_collision():
    e = make_engine()
    own = ((5, 2),)
    e.start_feedback_window(own)
    e.ingest_report(report(sender=2, rs={own[0]: 1}))
    e.ingest_report(report(sender=3, rs={own[0]: 0}))
    result = e.evaluate_collision_feedback()
    assert result.reports == 2
    assert result.acknowledgement_ratio == 0.5
    assert result.collision_suspected


def test_feedback_above_threshold_does_not_trigger():
    e = make_engine()
    own = ((5, 2),)
    e.start_feedback_window(own)
    for sender in range(2, 7):
        e.ingest_report(report(sender=sender, rs={own[0]: 1}))
    result = e.evaluate_collision_feedback()
    assert result.acknowledgement_ratio == 1.0
    assert not result.collision_suspected


def test_no_feedback_does_not_invent_collision():
    e = make_engine()
    e.start_feedback_window(((5, 2),))
    result = e.evaluate_collision_feedback()
    assert result.acknowledgement_ratio is None
    assert not result.collision_suspected


def test_paper_dual_threshold_can_reject_distant_false_alarm():
    cfg = profile(
        "core", numerology=0, bandwidth_mhz=10, subchannels_per_slot=5,
        required_subchannels=1, rri_s=0.1, t1_s=0.001, t2_s=0.020,
        packet_delay_budget_s=0.1,
    )
    e = SORAEngine(
        Mode2ResourceSelector(cfg, seed=1), rri_s=0.1, slots_per_rri=100,
        subchannels_per_slot=5, seed=1, collision_threshold=0.8,
        collision_mode="paper_dual", strong_threshold=0.9,
        strong_neighbor_fraction=0.5,
    )
    own = ((5, 2),)
    e.start_feedback_window(own)
    # Closest half acknowledge, distant half do not: overall phi=0.5 but
    # strong-neighbor phi=1, so the dual detector must not declare collision.
    e.ingest_report(report(sender=2, distance=10, rs={own[0]: 1}))
    e.ingest_report(report(sender=3, distance=15, rs={own[0]: 1}))
    e.ingest_report(report(sender=4, distance=80, rs={own[0]: 0}))
    e.ingest_report(report(sender=5, distance=100, rs={own[0]: 0}))
    result = e.evaluate_collision_feedback()
    assert result.acknowledgement_ratio == 0.5
    assert result.strong_acknowledgement_ratio == 1.0
    assert not result.collision_suspected


def test_sora_map5_floor_is_applied_after_cooperative_map_with_three_db_relaxation():
    cfg = profile(
        "core", numerology=0, bandwidth_mhz=10, subchannels_per_slot=5,
        required_subchannels=1, rri_s=0.1, t1_s=0.001, t2_s=0.020,
        packet_delay_budget_s=0.1, enforce_minimum_candidate_percent=False,
    )
    seen_thresholds = []
    def distance_from_threshold(threshold_dbm):
        seen_thresholds.append(threshold_dbm)
        return 50.0

    e = SORAEngine(
        Mode2ResourceSelector(cfg, seed=7), rri_s=0.1, slots_per_rri=100,
        subchannels_per_slot=5, seed=7, enable_map_candidate_floor=True,
        map_candidate_floor_percent=5.0, map_relax_step_db=3.0,
        map_initial_threshold_dbm=-90.0,
        map_distance_from_threshold=distance_from_threshold,
    )
    nr = e.selector.candidate_set(0.0)
    # Leave only one cooperative candidate (<5% of the underlying candidate
    # space); mark the rest as distant two-hop restrictions.
    keep = nr.candidates[0]
    keep_phase = set(e.candidate_phase_resources(keep))
    for cand in nr.candidates[1:]:
        for key in e.candidate_phase_resources(cand):
            if key not in keep_phase:
                e._set_state(key, ResourceState.TWO_HOP, 0.0, 100.0, "relay")

    result = e.select(0.0)
    assert result.map_relaxations == 1
    assert seen_thresholds == [-87.0]
    assert result.cooperative_candidate_percent >= 5.0
    assert result.nr_candidates.relaxations == 0


def test_sora_map_floor_can_be_set_to_five_percent_without_nr_twenty_percent_floor():
    e = make_engine(seed=9)
    # Baseline helper still has the NR selector's own 20% configuration, but
    # SORA's post-map floor is an independent mechanism and is disabled here.
    assert e.map_candidate_floor_percent == 5.0
    assert not e.enable_map_candidate_floor


def _make_future_coord_engine(seed=11):
    cfg = profile(
        "core", numerology=0, bandwidth_mhz=10, subchannels_per_slot=5,
        required_subchannels=1, rri_s=0.1, t1_s=0.001, t2_s=0.020,
        packet_delay_budget_s=0.1,
    )
    return SORAEngine(
        Mode2ResourceSelector(cfg, seed=seed), rri_s=0.1, slots_per_rri=100,
        subchannels_per_slot=5, seed=seed, reserve_announced_future=True,
    )


def test_announced_future_is_direct_one_hop_only_and_not_rs_state():
    e = _make_future_coord_engine(11)
    e.ingest_report(report(sender=7, future=((7, 3),)))
    assert e.state((7, 3)) is ResourceState.FREE
    assert e.direct_future_resources(0.0) == {(7, 3)}
    assert (7, 3) not in e.snapshot(0.0)


def test_tentative_state_received_inside_rs_is_not_relayed():
    e = _make_future_coord_engine(12)
    e.ingest_report(report(sender=8, rs={(9, 2): int(ResourceState.TENTATIVE_FUTURE)}))
    assert e.state((9, 2)) is ResourceState.FREE


def test_fixed_budget_snapshot_excludes_direct_future_advertisements():
    e = _make_future_coord_engine(13)
    e.ingest_report(report(
        t=0.0, sender=2, current=((1, 0),), future=((2, 0),), rs={(3, 0): 1}
    ))
    snap = e.snapshot(0.0, max_entries=2)
    assert (2, 0) not in snap
    assert snap[(1, 0)] == int(ResourceState.ONE_HOP)
    assert all(int(v) in (0, 1, 2) for v in snap.values())


def test_changed_future_replaces_previous_direct_sender_entry():
    e = _make_future_coord_engine(15)
    e.ingest_report(report(t=0.0, sender=9, future=((7, 1),)))
    assert e.direct_future_resources(0.0) == {(7, 1)}
    e.ingest_report(report(t=0.05, sender=9, future=((8, 1),)))
    assert e.direct_future_resources(0.05) == {(8, 1)}
    assert e.state((7, 1)) is ResourceState.FREE
    assert e.state((8, 1)) is ResourceState.FREE


def test_direct_future_advertisement_expires_after_map_horizon():
    e = _make_future_coord_engine(16)
    e.ingest_report(report(t=0.0, sender=4, future=((5, 2),)))
    assert e.direct_future_resources(0.19) == {(5, 2)}
    assert e.direct_future_resources(0.201) == set()


def test_future_selector_avoids_direct_neighbor_future_when_clean_candidate_exists():
    e = _make_future_coord_engine(17)
    nr = e.selector.candidate_set(0.0)
    assert len(nr.candidates) > 1
    blocked_candidate = nr.candidates[0]
    blocked_phase = e.candidate_phase_resources(blocked_candidate)
    e.ingest_report(report(sender=21, future=blocked_phase))
    result = e.select_future(0.0, stream="sora")
    selected_phase = set(e.candidate_phase_resources(result.selected))
    assert selected_phase.isdisjoint(set(blocked_phase))


def test_sparse_missing_entry_is_not_treated_as_free_acknowledgement():
    e = make_engine()
    own = ((5, 2),)
    e.start_feedback_window(own)
    # A sparse/delta report that simply does not carry the resource is
    # ambiguous and must not be turned into an explicit FREE state.
    e.ingest_report(report(sender=2, rs={(9, 1): 1}))
    result = e.evaluate_collision_feedback()
    assert result.acknowledgement_ratio is None
    assert not result.collision_suspected
