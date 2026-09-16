from trace_io import infer_trace_dt, resample_vehicle_data, trace_summary


def test_one_second_trace_resamples_to_100ms():
    data = {
        "v0": [
            {"timestamp": 0.0, "position_x": 0.0, "position_y": 0.0, "speed": 10.0, "heading": 90.0},
            {"timestamp": 1.0, "position_x": 10.0, "position_y": 0.0, "speed": 10.0, "heading": 90.0},
        ]
    }
    out = resample_vehicle_data(data, 0.1)
    assert len(out["v0"]) == 11
    assert infer_trace_dt(out) == 0.1
    assert abs(out["v0"][5]["position_x"] - 5.0) < 1e-12
    assert out["v0"][-1]["timestamp"] == 1.0


def test_heading_interpolation_wraps_across_north():
    data = {
        "v0": [
            {"timestamp": 0.0, "position_x": 0.0, "position_y": 0.0, "speed": 1.0, "heading": 350.0},
            {"timestamp": 1.0, "position_x": 0.0, "position_y": 1.0, "speed": 1.0, "heading": 10.0},
        ]
    }
    out = resample_vehicle_data(data, 0.5)
    h = out["v0"][1]["heading"]
    assert min(abs(h), abs(h - 360.0)) < 1e-12


def test_discrete_edge_id_is_not_interpolated_or_invented():
    data = {
        "v0": [
            {"timestamp": 0.0, "position_x": 0.0, "position_y": 0.0, "speed": 1.0, "edge_id": "A"},
            {"timestamp": 1.0, "position_x": 1.0, "position_y": 0.0, "speed": 1.0, "edge_id": "B"},
        ]
    }
    out = resample_vehicle_data(data, 0.5)
    assert out["v0"][0]["edge_id"] == "A"
    assert out["v0"][1]["edge_id"] == "A"
    assert out["v0"][2]["edge_id"] == "B"


def test_large_missing_gap_is_not_bridged():
    data = {
        "v0": [
            {"timestamp": 0.0, "position_x": 0.0, "position_y": 0.0, "speed": 1.0},
            {"timestamp": 1.0, "position_x": 1.0, "position_y": 0.0, "speed": 1.0},
            {"timestamp": 5.0, "position_x": 5.0, "position_y": 0.0, "speed": 1.0},
        ]
    }
    out = resample_vehicle_data(data, 0.1, max_bridge_gap_s=1.5)
    times = [x["timestamp"] for x in out["v0"]]
    assert 2.0 not in times
    assert 5.0 in times


def test_trace_summary_reports_metadata():
    data = {
        "v0": [
            {"timestamp": 0.0, "position_x": 0.0, "position_y": 0.0, "speed": 0.0, "heading": 90.0, "edge_id": "A"},
            {"timestamp": 0.1, "position_x": 0.0, "position_y": 0.0, "speed": 0.0, "heading": 90.0, "edge_id": "A"},
        ]
    }
    summary = trace_summary(data)
    assert summary["dt_s"] == 0.1
    assert summary["has_edge_id"] is True
    assert summary["has_heading"] is True
