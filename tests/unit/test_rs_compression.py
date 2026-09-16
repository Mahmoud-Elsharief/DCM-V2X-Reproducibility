from sora.overhead import compare_full_state_encodings, compare_delta_encodings, rs_encoding_bytes


def _key(i):
    return (i // 5, i % 5)


def test_full_raw_and_dual_bitmap_are_127_bytes_for_500_resources():
    r = compare_full_state_encodings({}, rri_s=0.1, numerology=0, bandwidth_mhz=10)
    assert r["raw2"]["total_bytes"] == 127
    assert r["dual_bitmap"]["total_bytes"] == 127


def test_occupancy_hop_100_occupied_is_77_bytes_including_header():
    states = {_key(i): (1 if i % 2 == 0 else 2) for i in range(100)}
    r = compare_full_state_encodings(states, rri_s=0.1, numerology=0, bandwidth_mhz=10)
    # 500 occupancy bits + 100 hop bits + 16 header bits = 616 bits = 77 B.
    assert r["occupancy_hop"]["total_bytes"] == 77


def test_sparse_index_full_100_occupied_is_140_bytes():
    states = {_key(i): 1 for i in range(100)}
    r = compare_full_state_encodings(states, rri_s=0.1, numerology=0, bandwidth_mhz=10)
    # 100 * (9 index + 2 state) + 16 header = 1116 bits = 140 B.
    assert r["sparse_index"]["total_bytes"] == 140


def test_rice_gap_can_beat_sparse_index_for_regular_sparse_map():
    states = {_key(i): (1 if i % 2 == 0 else 2) for i in range(0, 500, 5)}
    r = compare_full_state_encodings(states, rri_s=0.1, numerology=0, bandwidth_mhz=10)
    assert r["rice_gap"]["total_bytes"] < r["sparse_index"]["total_bytes"]
    assert r["adaptive"]["total_bytes"] <= min(v["total_bytes"] for k, v in r.items() if k != "adaptive")


def test_clustered_map_is_compressible_by_rle():
    states = {_key(i): 1 for i in range(100, 200)}
    r = compare_full_state_encodings(states, rri_s=0.1, numerology=0, bandwidth_mhz=10)
    assert r["rle_elias"]["total_bytes"] < r["raw2"]["total_bytes"]


def test_delta_rice_gap_and_adaptive_work_with_explicit_free_transitions():
    changes = {_key(4): 0, _key(7): 1, _key(10): 2, _key(14): 0}
    r = compare_delta_encodings(changes, rri_s=0.1, numerology=0, bandwidth_mhz=10)
    assert set(r) == {"sparse_index", "change_bitmap", "rice_gap", "adaptive"}
    assert r["adaptive"]["total_bytes"] <= min(v["total_bytes"] for k, v in r.items() if k != "adaptive")


def test_rs_encoding_bytes_dispatches_full_and_delta():
    states = {_key(1): 1, _key(9): 2}
    full = rs_encoding_bytes(states, packet_mode="full", encoding="adaptive", rri_s=0.1, numerology=0, bandwidth_mhz=10)
    delta = rs_encoding_bytes({_key(1): 0}, packet_mode="delta", encoding="adaptive", rri_s=0.1, numerology=0, bandwidth_mhz=10)
    assert full["total_bytes"] > 0
    assert delta["total_bytes"] > 0
