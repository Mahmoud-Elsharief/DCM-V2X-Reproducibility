from synthetic_mobility import (
    HighwayMobilityConfig, UrbanMobilityConfig,
    generate_highway, generate_urban,
)
from mobility_source import load_or_generate_mobility
from trace_io import trace_summary


def test_highway_generator_is_reproducible_and_100ms():
    cfg = HighwayMobilityConfig(vehicles=8, duration_s=1.0, dt_s=0.1, seed=9)
    a = generate_highway(cfg)
    b = generate_highway(cfg)
    assert a == b
    s = trace_summary(a)
    assert s['vehicles'] == 8
    assert s['dt_s'] == 0.1
    assert s['has_edge_id'] is True


def test_highway_has_both_directions_and_shared_physical_street():
    d = generate_highway(HighwayMobilityConfig(
        vehicles=6, duration_s=0.1, lanes_per_direction=3, seed=1
    ))
    first = [v[0] for v in d.values()]
    assert {x['heading'] for x in first} == {90.0, 270.0}
    assert len({x['street_id'] for x in first}) == 1
    assert len({x['edge_id'] for x in first}) == 2


def test_urban_generator_stays_on_grid_and_has_street_ids():
    cfg = UrbanMobilityConfig(
        vehicles=12, duration_s=2.0, dt_s=0.1, blocks_x=4, blocks_y=4,
        block_length_m=100.0, turn_probability=0.7, seed=5,
    )
    d = generate_urban(cfg)
    for records in d.values():
        for r in records:
            x, y = r['position_x'], r['position_y']
            # A vehicle lies on a horizontal or vertical grid street.
            on_h = abs((y / 100.0) - round(y / 100.0)) < 1e-8
            on_v = abs((x / 100.0) - round(x / 100.0)) < 1e-8
            assert on_h or on_v
            assert r['street_id'].startswith('urban-')


def test_auto_mobility_falls_back_to_synthetic_when_trace_missing(tmp_path):
    d, meta = load_or_generate_mobility(
        scenario='urban', trace_path=str(tmp_path/'missing.csv'), source='auto',
        vehicles=5, duration_s=0.5, dt_s=0.1, seed=2,
    )
    assert meta['source'] == 'synthetic'
    assert len(d) == 5


def test_trace_mode_requires_existing_file(tmp_path):
    try:
        load_or_generate_mobility(
            scenario='highway', trace_path=str(tmp_path/'missing.csv'), source='trace'
        )
    except FileNotFoundError:
        pass
    else:
        raise AssertionError('trace mode must fail instead of silently generating')
