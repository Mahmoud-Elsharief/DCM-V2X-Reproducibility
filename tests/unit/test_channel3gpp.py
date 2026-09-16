import math

from nrv2x import (
    LinkState,
    V2VChannelConfig,
    V2VLargeScaleChannel,
    V2VScenario,
)


class DummyNode:
    def __init__(self, node_id, x, y=0.0, road_id=None):
        self.node_id = node_id
        self.position = (x, y)
        self.road_id = road_id


def test_3gpp_path_loss_reference_values():
    h = V2VLargeScaleChannel(V2VChannelConfig(
        scenario=V2VScenario.HIGHWAY, shadowing=False, nlosv_blockage=False
    ))
    u = V2VLargeScaleChannel(V2VChannelConfig(
        scenario=V2VScenario.URBAN, shadowing=False, nlosv_blockage=False
    ))
    assert math.isclose(h.path_loss_db(500, LinkState.LOS), 101.78170598246241, abs_tol=1e-6)
    assert math.isclose(u.path_loss_db(500, LinkState.LOS), 97.85889743753675, abs_tol=1e-6)
    assert math.isclose(u.path_loss_db(500, LinkState.NLOS), 132.3742792015568, abs_tol=1e-6)


def test_los_probability_reference_points():
    h = V2VLargeScaleChannel(V2VChannelConfig(scenario=V2VScenario.HIGHWAY))
    u = V2VLargeScaleChannel(V2VChannelConfig(scenario=V2VScenario.URBAN))
    assert h.highway_los_probability(0) == 1.0
    assert math.isclose(h.highway_los_probability(500), 0.515, abs_tol=1e-12)
    assert math.isclose(h.highway_los_probability(1000), 0.015, abs_tol=1e-12)
    assert math.isclose(u.urban_los_probability(100), 1.05 * math.exp(-1.14), abs_tol=1e-12)


def test_urban_different_streets_are_nlos():
    ch = V2VLargeScaleChannel(V2VChannelConfig(
        scenario=V2VScenario.URBAN, shadowing=False, nlosv_blockage=False
    ))
    a = DummyNode('a', 0, road_id='edge-A')
    b = DummyNode('b', 50, road_id='edge-B')
    assert ch.state(a, b, 0.0) is LinkState.NLOS


def test_urban_no_street_metadata_does_not_invent_building_nlos():
    ch = V2VLargeScaleChannel(V2VChannelConfig(
        scenario=V2VScenario.URBAN, seed=4, shadowing=False, nlosv_blockage=False
    ))
    a = DummyNode('a', 0)
    b = DummyNode('b', 100)
    assert ch.state(a, b, 0.0) in (LinkState.LOS, LinkState.NLOSV)


def test_nlosv_blockage_is_nonnegative():
    ch = V2VLargeScaleChannel(V2VChannelConfig(
        scenario=V2VScenario.HIGHWAY, seed=7, shadowing=False, nlosv_blockage=True
    ))
    a = DummyNode('a', 0)
    b = DummyNode('b', 300)
    loss = ch.nlosv_blockage_loss_db(a, b, 0.0, 300, LinkState.NLOSV)
    assert loss >= 0.0


def test_shadow_sigma_table_values():
    h = V2VLargeScaleChannel(V2VChannelConfig(scenario=V2VScenario.HIGHWAY))
    u = V2VLargeScaleChannel(V2VChannelConfig(scenario=V2VScenario.URBAN))
    assert h.shadow_sigma_db(LinkState.LOS) == 3.3
    assert h.shadow_sigma_db(LinkState.NLOSV) == 3.8
    assert u.shadow_sigma_db(LinkState.LOS) == 5.2
    assert u.shadow_sigma_db(LinkState.NLOSV) == 5.3
    assert u.shadow_sigma_db(LinkState.NLOS) == 6.8


def test_link_budget_is_reciprocal_for_static_link():
    ch = V2VLargeScaleChannel(V2VChannelConfig(
        scenario=V2VScenario.HIGHWAY, seed=11
    ))
    a = DummyNode('a', 0)
    b = DummyNode('b', 80)
    ab = ch.link_budget(a, b, 0.2, 23.0)
    ba = ch.link_budget(b, a, 0.2, 23.0)
    assert ab.state == ba.state
    assert math.isclose(ab.total_loss_db, ba.total_loss_db, abs_tol=1e-12)
    assert math.isclose(ab.received_power_dbm, ba.received_power_dbm, abs_tol=1e-12)


def test_heading_fallback_classifies_same_and_cross_streets():
    ch = V2VLargeScaleChannel(V2VChannelConfig(
        scenario=V2VScenario.URBAN, shadowing=False, nlosv_blockage=False
    ))
    a = DummyNode('a', 0, 0)
    b = DummyNode('b', 100, 0)
    a.heading = 90.0  # east in SUMO convention
    b.heading = 270.0 # west, same street axis
    assert ch._same_street(a, b) is True

    c = DummyNode('c', 50, -50)
    d = DummyNode('d', 50, 50)
    c.heading = 0.0   # north
    d.heading = 180.0 # south, same vertical street
    assert ch._same_street(c, d) is True
    assert ch._same_street(a, c) is False


def test_explicit_edge_transition_forces_nlos_at_next_location_update():
    ch = V2VLargeScaleChannel(V2VChannelConfig(
        scenario=V2VScenario.URBAN, seed=3, shadowing=False, nlosv_blockage=False
    ))
    a = DummyNode('a', 0, 0, road_id='edge-A')
    b = DummyNode('b', 50, 0, road_id='edge-A')
    assert ch.state(a, b, 0.0) in (LinkState.LOS, LinkState.NLOSV)
    b.road_id = 'edge-B'
    # NLOS transition caused by street geometry is checked immediately; it is
    # independent of the optional 1-s LOS<->NLOSv redraw cadence.
    assert ch.state(a, b, 0.1) is LinkState.NLOS
