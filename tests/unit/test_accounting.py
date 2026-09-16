from pathlib import Path
from types import SimpleNamespace

from event import Event, EventScheduler
from metrics import summarize_run


def test_scheduler_does_not_execute_event_at_horizon():
    hit = []
    s = EventScheduler(1.0)
    s.schedule_event(1.0, Event(1.0, lambda: hit.append(True)))
    s.run()
    assert hit == []


def test_application_delivery_penalizes_untransmitted_packets(tmp_path):
    out = tmp_path / "rx.csv"
    out.write_text(
        "timestamp,sender_id,receiver_id,packet_id,distance,success\n"
        "0.1,0,1,p0,10,1\n"
        "0.2,0,1,p1,10,0\n"
    )
    node0 = SimpleNamespace(
        mac_layer=object(), application_packets_generated=4, tx_opportunities=3,
        actual_transmissions=2, deferred_transmissions=1, dropped_packets=0,
        defer_delays=[0.0, 0.1],
    )
    node1 = SimpleNamespace(
        mac_layer=object(), application_packets_generated=0, tx_opportunities=0,
        actual_transmissions=0, deferred_transmissions=0, dropped_packets=0,
        defer_delays=[],
    )
    nm = SimpleNamespace(nodes={0: node0, 1: node1})
    m = summarize_run(out, nm)
    assert m["radio_eval_success_pct"] == 50.0
    assert m["tx_pair_prr_pct"] == 50.0
    assert m["application_delivery_pct"] == 25.0
    assert m["tx_completion_pct"] == 50.0
    assert m["pending_generated_packets"] == 2


def test_pir_uses_successful_receiver_arrival_times(tmp_path):
    out = tmp_path / "rx.csv"
    out.write_text(
        "timestamp,sender_id,receiver_id,packet_id,distance,success\n"
        "0.1,0,1,p0,10,1\n"
        "0.2,0,1,p1,10,0\n"
        "0.4,0,1,p2,10,1\n"
        "0.9,0,1,p3,10,1\n"
    )
    n = SimpleNamespace(
        mac_layer=object(), application_packets_generated=4, tx_opportunities=4,
        actual_transmissions=4, deferred_transmissions=0, dropped_packets=0,
        defer_delays=[0.0] * 4,
    )
    peer = SimpleNamespace(
        mac_layer=object(), application_packets_generated=0, tx_opportunities=0,
        actual_transmissions=0, deferred_transmissions=0, dropped_packets=0,
        defer_delays=[],
    )
    m = summarize_run(out, SimpleNamespace(nodes={0: n, 1: peer}))
    assert m["mean_pir_ms"] == 400.0  # intervals: 300 ms and 500 ms
