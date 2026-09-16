from sora import HybridRSPacket, HybridRSTransmitter, HybridRSReceiver


def test_hybrid_full_then_delta_reconstructs_complete_state_and_zero_transition():
    tx = HybridRSTransmitter(full_interval_s=1.0)
    rx = HybridRSReceiver()
    a = {(0, 0): 1, (0, 1): 0, (0, 2): 2}
    p0 = tx.encode(0.0, a)
    assert p0.mode == 'full'
    state, valid = rx.ingest('A', p0)
    assert valid and state == a

    b = {(0, 0): 0, (0, 1): 1, (0, 2): 2}
    p1 = tx.encode(0.1, b)
    assert p1.mode == 'delta'
    # Explicit release to FREE and new occupancy are both carried.
    assert p1.states[(0, 0)] == 0
    assert p1.states[(0, 1)] == 1
    state, valid = rx.ingest('A', p1)
    assert valid and state == b


def test_hybrid_missing_intermediate_delta_is_recoverable_from_full_baseline():
    tx = HybridRSTransmitter(full_interval_s=1.0)
    rx = HybridRSReceiver()
    p0 = tx.encode(0.0, {(0, 0): 1, (0, 1): 0})
    _, valid = rx.ingest('A', p0)
    assert valid
    _missed = tx.encode(0.1, {(0, 0): 0, (0, 1): 0})
    p2 = tx.encode(0.2, {(0, 0): 0, (0, 1): 1})
    state, valid = rx.ingest('A', p2)
    assert valid
    assert state[(0, 0)] == 0 and state[(0, 1)] == 1


def test_hybrid_delta_without_full_is_rejected():
    rx = HybridRSReceiver()
    p = HybridRSPacket('delta', epoch=3, seq=2, states={(0, 0): 1})
    state, valid = rx.ingest('A', p)
    assert not valid and state is None
