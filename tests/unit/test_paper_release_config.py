from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs" / "paper_full_strict_mcs11.json"
RUN_CASE = ROOT / "experiments" / "run_case.py"


def test_final_manuscript_configuration_is_frozen():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert cfg["carrier_frequency_ghz"] == 5.89
    assert cfg["duration_s"] == 30.0
    assert cfg["mcs"] == 11
    assert cfg["application_bytes"] == 300
    assert cfg["sora_packet_bytes"] == 349
    assert cfg["nrv2x_packet_bytes"] == 300
    assert cfg["sora_rs_control_budget_bytes"] == 49
    assert cfg["bandwidth_mhz"] == 10
    assert cfg["numerology"] == 0
    assert cfg["rri_s"] == 0.1
    assert cfg["subchannel_size_prb"] == 10
    assert cfg["subchannels_per_slot"] == 5
    assert cfg["resource_positions_per_rri"] == 500
    assert cfg["sensing_threshold_dbm"] == -90.0
    assert cfg["tx_power_dbm"] == 23.0
    assert cfg["noise_figure_db"] == 9.0
    assert cfg["future_resource"]["advertised_to_direct_one_hop"] is True
    assert cfg["future_resource"]["relayed_as_two_hop_rs_state"] is False
    assert cfg["sora"]["collision_threshold"] == 0.8
    assert cfg["sora"]["strong_threshold"] == 0.9
    assert cfg["sora"]["rs_mode"] == "hybrid"
    assert cfg["sora"]["rs_full_interval_s"] == 1.0
    assert cfg["sora"]["rs_logical_encoding"] == "rice_gap"
    assert cfg["sora"]["rs_delta_reference"] == "latest_full_epoch"
    assert cfg["sora"]["expected_rc_range_at_100ms"] == [50, 150]
    assert cfg["nrv2x"]["expected_rc_range_at_100ms"] == [5, 15]


def _dry_run(protocol: str) -> str:
    p = subprocess.run(
        [
            sys.executable, str(RUN_CASE), "--protocol", protocol,
            "--scenario", "highway", "--density", "low", "--dry-run",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return p.stdout


def test_dry_run_freezes_sora_packet_refresh_and_carrier():
    out = _dry_run("sora")
    assert "--packet-bytes 349" in out
    assert "--carrier-frequency-ghz 5.89" in out
    assert "--rs-full-interval 1.0" in out
    assert "--advertise-future-resource" in out
    assert "--collision-threshold 0.8" in out
    assert "--strong-threshold 0.9" in out


def test_dry_run_freezes_nr_packet_and_carrier():
    out = _dry_run("nr")
    assert "--packet-bytes 300" in out
    assert "--carrier-frequency-ghz 5.89" in out
    assert "--rc-mode standard" in out
    assert "--rs-full-interval" not in out
