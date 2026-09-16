import random

from sora.rs_codec import encode_rs, decode_rs


def key(i):
    return (i // 5, i % 5)


def full_map(seed=7, n=100):
    rng = random.Random(seed)
    ids = sorted(rng.sample(range(500), n))
    return {key(idx): (1 if j % 2 == 0 else 2) for j, idx in enumerate(ids)}


def delta_map(seed=9, n=20):
    rng = random.Random(seed)
    ids = sorted(rng.sample(range(500), n))
    return {key(idx): (j % 3) for j, idx in enumerate(ids)}


def test_all_full_codecs_roundtrip_exactly():
    states = full_map()
    for method in ("raw2", "dual_bitmap", "occupancy_hop", "sparse_index", "rle_elias", "huffman_state", "rice_gap", "adaptive"):
        encoded = encode_rs(states, mode="full", method=method)
        mode, selected, decoded = decode_rs(encoded.wire)
        assert mode == "full"
        assert decoded == states
        if method != "adaptive":
            assert selected == method


def test_all_delta_codecs_roundtrip_exactly_including_free_transitions():
    changes = delta_map()
    assert 0 in changes.values()
    for method in ("sparse_index", "change_bitmap", "rice_gap", "adaptive"):
        encoded = encode_rs(changes, mode="delta", method=method)
        mode, selected, decoded = decode_rs(encoded.wire)
        assert mode == "delta"
        assert decoded == changes
        if method != "adaptive":
            assert selected == method


def test_empty_rice_gap_full_roundtrip_and_size_model_match():
    from sora.rs_codec import encode_rs, decode_rs
    c = encode_rs({}, mode="full", method="rice_gap", rri_s=0.1, numerology=0, bandwidth_mhz=10)
    mode, method, decoded = decode_rs(c.wire, rri_s=0.1, numerology=0, bandwidth_mhz=10)
    assert mode == "full"
    assert method == "rice_gap"
    assert decoded == {}
    assert c.total_bytes == 3  # 2-byte common header + 4-bit k field


def test_empty_rice_gap_delta_roundtrip_and_size_model_match():
    from sora.rs_codec import encode_rs, decode_rs
    c = encode_rs({}, mode="delta", method="rice_gap", rri_s=0.1, numerology=0, bandwidth_mhz=10)
    mode, method, decoded = decode_rs(c.wire, rri_s=0.1, numerology=0, bandwidth_mhz=10)
    assert mode == "delta"
    assert method == "rice_gap"
    assert decoded == {}
    assert c.total_bytes == 3  # 2-byte common header + 4-bit k field
