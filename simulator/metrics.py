from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Iterable


def _percentile(values, q: float):
    vals = sorted(float(v) for v in values)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * float(q)
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def summarize_run(output_path, node_manager):
    """Summarize radio- and application-level accounting.

    Metrics intentionally separate:
      * receiver-evaluation success (legacy CSV denominator),
      * PRR per actual broadcast TX (all other active nodes in denominator),
      * application delivery (generated packets in denominator), and
      * packet inter-reception intervals from successful receiver arrivals.

    The all-node denominators are appropriate for the fixed-population synthetic
    diagnostics used by the matched sweep. Distance-binned publication PRR is
    computed separately from per-receiver rows.
    """
    output = Path(output_path)
    attempts = 0
    successes = 0
    success_times = defaultdict(list)
    if output.exists():
        with output.open(newline="") as f:
            for row in csv.DictReader(f):
                attempts += 1
                ok = int(row["success"])
                successes += ok
                if ok:
                    success_times[(str(row["sender_id"]), str(row["receiver_id"]))].append(float(row["timestamp"]))

    nodes = [n for n in node_manager.nodes.values() if hasattr(n, "mac_layer")]
    n_nodes = len(nodes)
    generated = sum(getattr(n, "application_packets_generated", 0) for n in nodes)
    opportunities = sum(getattr(n, "tx_opportunities", 0) for n in nodes)
    actual_tx = sum(getattr(n, "actual_transmissions", 0) for n in nodes)
    deferred = sum(getattr(n, "deferred_transmissions", 0) for n in nodes)
    dropped = sum(getattr(n, "dropped_packets", 0) for n in nodes)
    pending = max(0, generated - actual_tx - dropped)

    tx_delays = [float(d) for n in nodes for d in getattr(n, "defer_delays", [])]
    positive_delays = [d for d in tx_delays if d > 1e-12]

    pir_intervals = []
    pair_mean_pir = []
    for times in success_times.values():
        times = sorted(times)
        ints = [b - a for a, b in zip(times, times[1:]) if b >= a]
        if ints:
            pir_intervals.extend(ints)
            pair_mean_pir.append(sum(ints) / len(ints))

    receiver_peers = max(0, n_nodes - 1)
    tx_pair_den = actual_tx * receiver_peers
    app_pair_den = generated * receiver_peers

    def pct(num, den):
        return round(100.0 * num / den, 4) if den else None

    return {
        "nodes": n_nodes,
        "receiver_evaluations": attempts,
        "successful_receptions": successes,
        "radio_eval_success_pct": pct(successes, attempts),
        "application_packets_generated": generated,
        "tx_opportunities": opportunities,
        "actual_transmissions": actual_tx,
        "deferred_transmissions": deferred,
        "dropped_packets": dropped,
        "pending_generated_packets": pending,
        "tx_completion_pct": pct(actual_tx, generated),
        "tx_pair_prr_pct": pct(successes, tx_pair_den),
        "application_delivery_pct": pct(successes, app_pair_den),
        "mean_tx_delay_ms": round(1000.0 * sum(tx_delays) / len(tx_delays), 4) if tx_delays else None,
        "mean_nonzero_defer_ms": round(1000.0 * sum(positive_delays) / len(positive_delays), 4) if positive_delays else 0.0,
        "p95_tx_delay_ms": round(1000.0 * _percentile(tx_delays, 0.95), 4) if tx_delays else None,
        "pir_interval_count": len(pir_intervals),
        "mean_pir_ms": round(1000.0 * sum(pir_intervals) / len(pir_intervals), 4) if pir_intervals else None,
        "p95_pir_ms": round(1000.0 * _percentile(pir_intervals, 0.95), 4) if pir_intervals else None,
        "mean_pair_pir_ms": round(1000.0 * sum(pair_mean_pir) / len(pair_mean_pir), 4) if pair_mean_pir else None,
    }
