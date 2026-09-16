from sora import future_resources_on_air
from sora.overhead import overhead_percentages


def test_future_resource_is_advertised_by_default():
    future = ((7, 3),)
    assert future_resources_on_air(future, enabled=True) == future


def test_future_advertisement_can_be_disabled_only_for_diagnostic_ablation():
    future = ((7, 3),)
    assert future_resources_on_air(future, enabled=True, advertise=True) == future
    assert future_resources_on_air(future, enabled=True, advertise=False) == ()
    assert future_resources_on_air(future, enabled=False, advertise=True) == ()


def test_final_packet_accounting_is_300_plus_49_bytes():
    r = overhead_percentages(300, 49)
    assert r["total_bytes"] == 349
    assert round(r["overhead_vs_application_pct"], 4) == 16.3333
    assert round(r["overhead_fraction_of_total_pct"], 4) == 14.0401
