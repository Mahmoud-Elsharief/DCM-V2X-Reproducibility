from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path

from channel import ChannelModel
from event import EventScheduler
from mobility_source import load_or_generate_mobility
from node_manager import NodeManager
from trace_io import trace_summary
from metrics import summarize_run
from phy_variants import apply_phy_variant
from nrv2x import resource_sizing, system_level_sinr_threshold_db


def run(args):
    scenario = "urban" if args.scenario in ("urban", "city") else "highway"
    os.environ["NRV2X_FC_GHZ"] = str(args.carrier_frequency_ghz)
    apply_phy_variant(args.phy_variant)
    vehicle_data, mobility_meta = load_or_generate_mobility(
        scenario=scenario,
        trace_path=args.trace,
        source=args.mobility_source,
        vehicles=args.vehicles,
        duration_s=args.duration,
        dt_s=args.mobility_dt,
        seed=args.seed,
        resample_trace=args.resample_trace,
        sumo_config=args.sumo_config,
        sumo_begin_s=args.sumo_begin,
        sumo_gui=args.sumo_gui,
        sumo_cache_trace=args.sumo_cache_trace,
    )
    os.environ["NRV2X_PROFILE"] = args.profile
    os.environ["NRV2X_SCENARIO"] = scenario
    os.environ["NRV2X_CHANNEL_SEED"] = str(args.seed)
    os.environ["NRV2X_RUN_SEED"] = str(args.seed)
    os.environ["NRV2X_RECEPTION_SEED"] = str(args.seed)
    os.environ["NRV2X_MCS_INDEX"] = str(args.mcs)
    os.environ["NRV2X_PACKET_BYTES"] = str(args.packet_bytes)
    os.environ["NRV2X_RECEPTION_MODEL"] = args.reception_model
    os.environ["NRV2X_SL_PRIORITY"] = str(args.sl_priority)
    os.environ["NRV2X_PDB_S"] = str(args.pdb)
    os.environ["NRV2X_T2_S"] = str(args.t2)
    os.environ["NRV2X_RC_MODE"] = args.rc_mode
    os.environ["NRV2X_RC_FACTOR"] = str(args.rc_factor)
    os.environ["NRV2X_TX_POWER_MODE"] = args.tx_power_mode
    os.environ["NRV2X_NOISE_FIGURE_DB"] = str(args.noise_figure_db)
    os.environ["NRV2X_RECEIVER_SENSITIVITY_MODE"] = args.receiver_sensitivity_mode
    if args.receiver_power_gate_dbm is not None:
        os.environ["NRV2X_RECEIVER_POWER_GATE_DBM"] = str(args.receiver_power_gate_dbm)

    # Keep the submitted simulator as an explicit bridge, but use published
    # resource-width anchors by default for reconstructed Core/Full.
    sizing_mode = args.resource_sizing
    if sizing_mode == "auto":
        sizing_mode = "fixed"
    if sizing_mode in ("fixed", "legacy"):
        sizing = resource_sizing(
            sizing_mode, args.mcs, args.packet_bytes,
            subchannel_size_prb=args.subchannel_size_prb,
            legacy_threshold_fallback_db=args.sinr_threshold,
        )
        sinr_threshold = (
            float(args.sinr_threshold) if args.sinr_threshold is not None
            else float(sizing.sinr_threshold_db)
        )
    else:
        sinr_threshold = (
            float(args.sinr_threshold) if args.sinr_threshold is not None
            else system_level_sinr_threshold_db(args.mcs, args.packet_bytes)
        )
        sizing = resource_sizing(
            "published", args.mcs, args.packet_bytes,
            subchannel_size_prb=args.subchannel_size_prb,
            published_threshold_db=sinr_threshold,
        )
    required_subchannels = (
        sizing.required_subchannels if args.subchannels is None
        else int(args.subchannels)
    )
    subchannels_per_slot = 5 * max(1, args.bandwidth_mhz // 10) if args.numerology == 0 else (2 if args.numerology == 1 else 1) * max(1, args.bandwidth_mhz // 10)
    subchannel_bw_hz = args.bandwidth_mhz * 1e6 / max(1, subchannels_per_slot)
    noise_per_subchannel_dbm = -174.0 + 10.0 * math.log10(subchannel_bw_hz) + args.noise_figure_db
    effective_sensitivity_per_subchannel_dbm = noise_per_subchannel_dbm + sinr_threshold
    equivalent_total_sensitivity_dbm = effective_sensitivity_per_subchannel_dbm + 10.0 * math.log10(max(1, required_subchannels))
    per_subchannel_tx_power_dbm = (
        args.tx_power_dbm - 10.0 * math.log10(max(1, required_subchannels))
        if args.tx_power_mode == "total" else args.tx_power_dbm
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    nm = NodeManager(vehicle_data)
    scheduler = EventScheduler(args.duration, nm)
    channel = ChannelModel(seed=args.seed, noise_figure_db=args.noise_figure_db)
    nm.schedule_vehicle_initialization(
        scheduler, channel,
        args.bandwidth_mhz, args.numerology, args.rri,
        0.5, 0.5, 0.0, required_subchannels,
        1, 20, True, 0,
        args.sensing_threshold_dbm, args.tx_power_dbm,
        -47.8, 2.3, sinr_threshold, str(output),
    )
    scheduler.run()

    metrics = summarize_run(output, nm)
    return {
        "profile": args.profile,
        "phy_variant": args.phy_variant,
        "scenario": scenario,
        "carrier_frequency_ghz": args.carrier_frequency_ghz,
        "mobility": mobility_meta,
        "trace": trace_summary(vehicle_data),
        "mcs": args.mcs,
        "packet_bytes": args.packet_bytes,
        "resource_sizing": sizing_mode,
        "required_prb": sizing.required_prb,
        "subchannel_size_prb": args.subchannel_size_prb,
        "required_subchannels": required_subchannels,
        "sinr_threshold_db": sinr_threshold,
        "subchannels_per_slot": subchannels_per_slot,
        "subchannel_bandwidth_hz": subchannel_bw_hz,
        "noise_per_subchannel_dbm": noise_per_subchannel_dbm,
        "effective_sensitivity_per_subchannel_dbm": effective_sensitivity_per_subchannel_dbm,
        "equivalent_total_sensitivity_dbm": equivalent_total_sensitivity_dbm,
        "per_subchannel_tx_power_dbm": per_subchannel_tx_power_dbm,
        "nr_v2x_n47_10mhz_refsens_dbm": -92.5,
        "reception_model": args.reception_model,
        "sl_priority": args.sl_priority,
        "rc_mode": args.rc_mode,
        "tx_power_mode": args.tx_power_mode,
        "noise_figure_db": args.noise_figure_db,
        "receiver_sensitivity_mode": args.receiver_sensitivity_mode,
        "receiver_power_gate_dbm": args.receiver_power_gate_dbm,
        "rc_factor": args.rc_factor if args.rc_mode == "factor10" else 1.0,
        "attempts": metrics["receiver_evaluations"],
        "success_pct": metrics["radio_eval_success_pct"],
        **metrics,
        "output": str(output),
    }


def main():
    ap = argparse.ArgumentParser(description="Run reconstructed NR-V2X with trace or synthetic fallback mobility")
    ap.add_argument("--profile", choices=["lite", "core_no_reeval", "core", "full"], default="full")
    ap.add_argument("--phy-variant", choices=["strict", "relaxed", "urban_relaxed1", "urban_relaxed2"], default="strict",
                    help="Controlled Full-PHY sensitivity preset; Core/Lite ignore most 3GPP channel switches")
    ap.add_argument("--scenario", choices=["highway", "urban", "city"], default="highway")
    ap.add_argument("--mobility-source", choices=["auto", "sumo", "trace", "synthetic"], default="auto")
    ap.add_argument("--trace", default=None, help="Saved SUMO/SORA mobility CSV")
    ap.add_argument("--sumo-config", default=None, help="SUMO .sumocfg for live-SUMO mode")
    ap.add_argument("--sumo-begin", type=float, default=0.0)
    ap.add_argument("--sumo-gui", action="store_true")
    ap.add_argument("--sumo-cache-trace", default=None,
                    help="Optional CSV path to retain the live SUMO trajectory for exact replay")
    ap.add_argument("--resample-trace", action="store_true", help="Bridge historical trace to --mobility-dt")
    ap.add_argument("--mobility-dt", type=float, default=0.1)
    ap.add_argument("--vehicles", type=int, default=100)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mcs", type=int, default=11)
    ap.add_argument("--packet-bytes", type=int, default=150, help="Paper default is 300 B; published interpolation is validated for 190..350 B")
    ap.add_argument("--carrier-frequency-ghz", type=float, default=5.89,
                    help="Carrier frequency used by the detailed V2V channel model")
    ap.add_argument("--sinr-threshold", type=float, default=None,
                    help="Explicit override; otherwise legacy or published model supplies the threshold")
    ap.add_argument("--reception-model", choices=["threshold", "logistic"], default="threshold")
    ap.add_argument("--sl-priority", type=int, choices=range(1, 9), default=1)
    ap.add_argument("--pdb", type=float, default=0.100)
    ap.add_argument("--t2", type=float, default=0.100)
    ap.add_argument("--bandwidth-mhz", type=int, default=10)
    ap.add_argument("--numerology", type=int, default=0)
    ap.add_argument("--rri", type=float, default=0.1)
    ap.add_argument("--resource-sizing", choices=["auto", "fixed", "legacy", "published"], default="auto",
                    help="auto: common fixed 2/1-subchannel sizing for all profiles; published remains optional")
    ap.add_argument("--subchannel-size-prb", type=int, default=10)
    ap.add_argument("--subchannels", type=int, default=None,
                    help="Explicit override of required subchannels (diagnostic only)")
    ap.add_argument("--sensing-threshold-dbm", type=float, default=-90.0)
    ap.add_argument("--tx-power-dbm", type=float, default=23.0)
    ap.add_argument("--rc-mode", choices=["factor10", "standard"], default="standard",
                    help="NR-V2X RC policy; standard is the default (5-15 at RRI=100 ms)")
    ap.add_argument("--rc-factor", type=float, default=10.0,
                    help="Multiplier used when --rc-mode factor10 (default 10)")
    ap.add_argument("--tx-power-mode", choices=["total", "per_subchannel"], default="total",
                    help="total: 23 dBm is total UE power and is split across occupied subchannels; per_subchannel is diagnostic only")
    ap.add_argument("--noise-figure-db", type=float, default=9.0)
    ap.add_argument("--receiver-sensitivity-mode", choices=["implicit", "derived", "fixed"], default="implicit",
                    help="implicit: SINR threshold + thermal noise define sensitivity; derived: additionally gate at N+SINR threshold; fixed: use explicit dBm gate")
    ap.add_argument("--receiver-power-gate-dbm", type=float, default=None,
                    help="Used only with --receiver-sensitivity-mode fixed")
    ap.add_argument("--output", default="nrv2x_results.csv")
    args = ap.parse_args()
    print(json.dumps(run(args), indent=2))


if __name__ == "__main__":
    main()
