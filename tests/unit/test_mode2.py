from nrv2x import CandidateResource, Mode2ResourceSelector, SensingRecord, profile


def short_core(**kw):
    base = dict(
        t1_s=0.001,
        t2_s=0.010,
        packet_delay_budget_s=0.010,
        selection_t2min_base_slots_by_priority={1: 1},
        subchannels_per_slot=1,
        required_subchannels=1,
    )
    base.update(kw)
    return profile("core", **base)


def test_no_sensing_all_candidates_available():
    cfg = profile(
        "core", t1_s=0.001, t2_s=0.003, packet_delay_budget_s=0.003,
        selection_t2min_base_slots_by_priority={1: 1},
        subchannels_per_slot=3, required_subchannels=1,
    )
    sel = Mode2ResourceSelector(cfg)
    result = sel.candidate_set(0.0)
    # slots 1,2,3 x 3 frequency candidates
    assert result.total_candidates == 9
    assert len(result.candidates) == 9
    assert result.relaxations == 0


def test_periodic_sci_projects_reservation_into_selection_window():
    cfg = profile(
        "core", t1_s=0.001, t2_s=0.210, packet_delay_budget_s=0.210,
        selection_t2min_base_slots_by_priority={1: 1},
        subchannels_per_slot=1, required_subchannels=1,
    )
    sel = Mode2ResourceSelector(cfg)
    # Historical observation at slot 1000 projects with 100-ms RRI into 1300,1400.
    sel.observe_sci(SensingRecord(1.000, 1000, (0,), -70.0, tx_id=1, reservation_period_s=0.100))
    result = sel.candidate_set(1.200)
    slots = {c.abs_slot for c in result.candidates}
    assert 1300 not in slots
    assert 1400 not in slots
    assert 1299 in slots
    assert 1301 in slots


def test_half_duplex_discards_sci_observation_in_own_tx_slot():
    cfg = short_core()
    sel = Mode2ResourceSelector(cfg)
    sel.mark_own_tx(5)
    accepted = sel.observe_sci(SensingRecord(0.005, 5, (0,), -60.0, tx_id=9))
    assert accepted is False
    assert sel.sensing_history == ()


def test_lite_keeps_same_slot_observation_without_half_duplex_filter():
    cfg = profile("lite", t1_s=0.001, t2_s=0.010, subchannels_per_slot=1)
    sel = Mode2ResourceSelector(cfg)
    sel.mark_own_tx(5)
    accepted = sel.observe_sci(SensingRecord(0.005, 5, (0,), -60.0, tx_id=9))
    assert accepted is True


def test_three_db_relaxation_recovers_minimum_20_percent():
    # Candidate slots are 1201..1210.  Historical slots 201..208 project to
    # those candidates with a 1-second reservation period.
    cfg = short_core(rsrp_threshold_dbm=-90.0, minimum_candidate_percent=20.0)
    sel = Mode2ResourceSelector(cfg)
    for i, slot in enumerate(range(201, 209), start=1):
        sel.observe_sci(SensingRecord(slot / 1000.0, slot, (0,), -88.0,
                                     tx_id=i, reservation_period_s=1.0))
    result = sel.candidate_set(1.200)
    assert len(result.candidates) == 2
    assert result.relaxations == 0

    # Nine exclusions leave 10%, forcing +3 dB. At -87 dBm the -88 dBm
    # observations fall below threshold and no longer exclude.
    sel.observe_sci(SensingRecord(0.209, 209, (0,), -88.0, tx_id=9, reservation_period_s=1.0))
    result2 = sel.candidate_set(1.200)
    assert result2.relaxations == 1
    assert result2.threshold_dbm == -87.0
    assert len(result2.candidates) == 10


def test_reevaluation_replaces_newly_excluded_resource():
    cfg = short_core(t2_s=0.005, packet_delay_budget_s=0.005)
    sel = Mode2ResourceSelector(cfg, seed=7)
    chosen = CandidateResource(1203, (0,))
    # Historical SCI at 203 with 1s RRI projects exactly onto 1203.
    sel.observe_sci(SensingRecord(0.203, 203, (0,), -60.0, tx_id=5, reservation_period_s=1.0))
    rr = sel.reevaluate(chosen, 1.200)
    assert rr.changed is True
    assert rr.selected != chosen
    assert rr.selected in rr.candidate_set.candidates


def test_core_no_reeval_retains_selected_resource():
    cfg = profile(
        "core_no_reeval", t1_s=0.001, t2_s=0.005,
        packet_delay_budget_s=0.005,
        selection_t2min_base_slots_by_priority={1: 1},
        subchannels_per_slot=1,
    )
    sel = Mode2ResourceSelector(cfg, seed=7)
    chosen = CandidateResource(1203, (0,))
    sel.observe_sci(SensingRecord(0.203, 203, (0,), -60.0, tx_id=5, reservation_period_s=1.0))
    rr = sel.reevaluate(chosen, 1.200)
    assert rr.changed is False
    assert rr.selected == chosen


def test_sensing_window_excludes_recent_tproc0_processing_gap():
    cfg = short_core()
    sel = Mode2ResourceSelector(cfg)
    # mu=0 => Tproc,0 = 1 slot = 1 ms; at now 1.200 cutoff is 1.199.
    old = SensingRecord(1.198, 1198, (0,), -60, tx_id=1)
    too_recent = SensingRecord(1.1995, 1199, (0,), -60, tx_id=2)
    sel.observe_sci(old)
    sel.observe_sci(too_recent)
    valid = sel._valid_sensing_records(1.200)
    assert old in valid
    assert too_recent not in valid


def test_tproc_and_t3_follow_release16_table():
    expected0 = {0: 1, 1: 1, 2: 2, 3: 4}
    expected1 = {0: 3, 1: 5, 2: 9, 3: 17}
    for mu in range(4):
        cfg = profile("core", numerology=mu)
        assert cfg.t_proc0_slots == expected0[mu]
        assert cfg.t_proc1_slots == expected1[mu]
        assert abs(cfg.t3_s - expected1[mu] * cfg.slot_duration_s) < 1e-15
        assert cfg.effective_t1_s <= cfg.t_proc1_s


def test_priority_pair_rsrp_threshold_changes_exclusion():
    table = {(8, 1): -70.0, (8, 2): -90.0}
    cfg = short_core(priority_rsrp_thresholds_dbm=table)
    sel = Mode2ResourceSelector(cfg)
    # Priority-8 historical reservation maps onto first candidate slot 1201.
    sel.observe_sci(SensingRecord(0.201, 201, (0,), -80.0, tx_id=8,
                                 tx_priority=8, reservation_period_s=1.0))
    p1 = {c.abs_slot for c in sel.candidate_set(1.200, tx_priority=1).candidates}
    p2 = {c.abs_slot for c in sel.candidate_set(1.200, tx_priority=2).candidates}
    assert 1201 in p1      # -80 is below -70 => do not exclude
    assert 1201 not in p2  # -80 is above -90 => exclude


def test_rsrp_threshold_list_uses_pi_plus_pj_minus_one_times_8_order():
    table = {(3, 2): -76.0}
    cfg = short_core(priority_rsrp_thresholds_dbm=table)
    values = cfg.rsrp_threshold_list()
    # 1-based index i=pi+(pj-1)*8 = 3+8 = 11 => Python index 10.
    assert values[10] == -76.0


def test_rsrp_rrc_code_mapping_includes_minus_90_dbm():
    from nrv2x import rrc_rsrp_code_to_dbm, rsrp_dbm_to_rrc_code
    assert rsrp_dbm_to_rrc_code(-90.0) == 20
    assert rrc_rsrp_code_to_dbm(20) == -90.0
    assert rrc_rsrp_code_to_dbm(0) == float('-inf')
    assert rrc_rsrp_code_to_dbm(66) == float('inf')


def test_map_only_candidate_rule_skips_20_percent_relaxation():
    # Nine of ten candidates are excluded at the base -90 dBm threshold.
    # Standard NR behavior relaxes +3 dB to recover >=20%; SORA map-only
    # deliberately keeps the single remaining candidate and does not relax.
    cfg = short_core(
        rsrp_threshold_dbm=-90.0,
        minimum_candidate_percent=20.0,
        enforce_minimum_candidate_percent=False,
    )
    sel = Mode2ResourceSelector(cfg)
    for i, slot in enumerate(range(201, 210), start=1):
        sel.observe_sci(SensingRecord(slot / 1000.0, slot, (0,), -88.0,
                                     tx_id=i, reservation_period_s=1.0))
    result = sel.candidate_set(1.200)
    assert len(result.candidates) == 1
    assert result.candidate_percent == 10.0
    assert result.relaxations == 0
    assert result.threshold_dbm == -90.0
