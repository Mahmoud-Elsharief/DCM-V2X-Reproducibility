from nrv2x.rc_policy import rc_bounds


def test_common_factor10_rc_at_100ms():
    assert rc_bounds(0.1, "factor10", 10) == (50, 150)


def test_standard_rc_at_100ms():
    assert rc_bounds(0.1, "standard", 10) == (5, 15)


def test_factor_is_configurable():
    assert rc_bounds(0.1, "factor10", 4) == (20, 60)
