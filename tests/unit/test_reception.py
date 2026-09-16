from nrv2x import (
    BLERCurve, BLERPoint, SidelinkReceptionConfig, SidelinkReceptionModel,
    fit_logistic_bler, system_level_sinr_threshold_db,
    legacy_required_subchannels,
)


def test_bler_monotonically_decreases_with_sinr():
    c = BLERCurve(midpoint_db=5.0, transition_db=1.0)
    vals = [c.bler(x) for x in (-5, 0, 5, 10, 15)]
    assert all(a > b for a, b in zip(vals, vals[1:]))
    assert abs(c.bler(5.0) - 0.5) < 1e-12


def test_pscch_and_pssch_are_separate_curves_in_logistic_mode():
    m = SidelinkReceptionModel(SidelinkReceptionConfig(
        pscch_mode="logistic", pssch_mode="logistic",
        pscch_midpoint_db=-3.0, pssch_midpoint_db=10.0, seed=4
    ))
    assert m.pscch_curve.success_probability(0.0) > m.pssch_curve.success_probability(0.0)


def test_reception_draw_is_reproducible_and_order_independent():
    m = SidelinkReceptionModel(SidelinkReceptionConfig(pssch_mode="logistic", seed=77))
    kwargs = dict(sender_id='a', receiver_id='b', packet_id=9, timestamp=1.2)
    a = m.decode_pssch(10.0, desired_power_dbm=-80, **kwargs)
    _ = m.decode_pssch(-10.0, desired_power_dbm=-80,
                       sender_id='x', receiver_id='y', packet_id=1, timestamp=3.4)
    b = m.decode_pssch(10.0, desired_power_dbm=-80, **kwargs)
    assert a == b


def test_receiver_power_gate_can_force_failure():
    m = SidelinkReceptionModel(SidelinkReceptionConfig(
        pssch_mode="threshold", pssch_threshold_db=-100,
        receiver_power_gate_dbm=-110, seed=1
    ))
    ok, bler, _ = m.decode_pssch(
        100.0, desired_power_dbm=-120,
        sender_id=1, receiver_id=2, packet_id=3, timestamp=0.0
    )
    assert ok is False
    assert bler == 1.0


def test_threshold_mode_is_deterministic_at_landmark():
    m = SidelinkReceptionModel(SidelinkReceptionConfig(
        pssch_mode="threshold", pssch_threshold_db=10.13,
    ))
    ident = dict(sender_id=1, receiver_id=2, packet_id=3, timestamp=0.0)
    assert m.decode_pssch(10.12, desired_power_dbm=-80, **ident)[0] is False
    assert m.decode_pssch(10.13, desired_power_dbm=-80, **ident)[0] is True


def test_frozen_system_level_mcs_landmarks_match_source_table():
    assert system_level_sinr_threshold_db(4, 190) == 2.33
    assert system_level_sinr_threshold_db(4, 350) == 2.11
    assert system_level_sinr_threshold_db(11, 190) == 10.18
    assert system_level_sinr_threshold_db(11, 350) == 10.13


def test_legacy_required_subchannel_mapping_is_explicit():
    assert legacy_required_subchannels(4) == 2
    assert legacy_required_subchannels(11) == 1


def test_logistic_calibration_recovers_known_curve():
    truth = BLERCurve(midpoint_db=4.0, transition_db=1.5)
    pts = [BLERPoint(x, truth.bler(x)) for x in (-1, 1, 3, 5, 7, 9)]
    fitted = fit_logistic_bler(pts)
    assert abs(fitted.midpoint_db - 4.0) < 1e-6
    assert abs(fitted.transition_db - 1.5) < 1e-6


def test_300_byte_threshold_interpolation():
    assert abs(system_level_sinr_threshold_db(4, 300) - 2.17875) < 1e-9
    assert abs(system_level_sinr_threshold_db(11, 300) - 10.145625) < 1e-9
