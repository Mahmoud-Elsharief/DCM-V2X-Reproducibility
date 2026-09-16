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
from sora.overhead import fixed_rs_ce_budget, overhead_percentages, bitmap_rs_bytes


def fixed_rs_ce_bytes(
    *,
    rri_s: float,
    numerology: int,
    bandwidth_mhz: int,
    max_entries: int,
    state_bits: int = 2,
    header_bytes: int = 2,
    hybrid_meta_bytes: int = 0,
    rs_mode: str = "snapshot",
) -> dict:
    """Backward-compatible wrapper around the explicit SORA overhead model."""
    result = fixed_rs_ce_budget(
        rri_s=rri_s,
        numerology=numerology,
        bandwidth_mhz=bandwidth_mhz,
        max_entries=max_entries,
        state_bits=state_bits,
        ce_header_bytes=header_bytes,
        hybrid_meta_bytes=hybrid_meta_bytes,
        rs_mode=rs_mode,
    )
    # Preserve legacy result keys used by prior scripts.
    result.setdefault("payload_bytes", result.get("entry_payload_bytes", 0))
    result.setdefault("header_bytes", result.get("ce_header_bytes", 0))
    return result


def run(args):
    # Controlled ARM6G abstraction used by the paper-comparison suite:
    # cooperative RS map/two-hop awareness, but no future resource and no
    # collision feedback/recovery; use the standard NR RC policy.
    if getattr(args, "arm6g_mode", False):
        args.disable_collision_feedback = True
        args.disable_future_resource = True
        args.disable_future_verification = True
        args.disable_future_promotion = True
        args.rc_mode = "standard"

    # Paper future-resource behavior: the prepared future resource is advertised
    # to directly receiving one-hop neighbors. It is not relayed in the ordinary
    # two-hop RS map. The disable option is retained only as a diagnostic ablation.

    # Optional physical accounting of the additional SORA RS control payload.
    # In fixed_cap mode every SPS packet is padded to a constant RS budget so
    # packet/TB size does not depend on instantaneous map occupancy.
    rs_on_air = None
    application_bytes = (
        int(args.application_bytes)
        if args.application_bytes is not None
        else int(args.packet_bytes)
    )
    if args.rs_on_air_mode == "fixed_cap":
        if int(args.rs_max_entries) <= 0:
            raise ValueError("--rs-on-air-mode fixed_cap requires --rs-max-entries > 0")
        rs_on_air = fixed_rs_ce_bytes(
            rri_s=args.rri,
            numerology=args.numerology,
            bandwidth_mhz=args.bandwidth_mhz,
            max_entries=args.rs_max_entries,
            state_bits=args.rs_state_bits,
            header_bytes=args.rs_ce_header_bytes,
            hybrid_meta_bytes=args.rs_hybrid_meta_bytes,
            rs_mode=args.rs_mode,
        )
        args.packet_bytes = application_bytes + int(rs_on_air["total_bytes"])

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

    os.environ["SORA_PROFILE"] = args.profile
    os.environ["SORA_SELECTOR_PROFILE"] = (args.profile if args.selector_profile == "auto" else args.selector_profile)
    os.environ["SORA_COLLISION_THRESHOLD"] = str(args.collision_threshold)
    os.environ["SORA_MIN_FEEDBACK_REPORTS"] = str(args.min_feedback_reports)
    os.environ["SORA_RS_EXPIRY_RRI"] = str(args.rs_expiry_rri)
    os.environ["SORA_RS_MODE"] = str(args.rs_mode)
    os.environ["SORA_RS_FULL_INTERVAL_S"] = str(args.rs_full_interval)
    os.environ["SORA_COLLISION_MODE"] = args.collision_mode
    os.environ["SORA_STRONG_THRESHOLD"] = str(args.strong_threshold)
    os.environ["SORA_STRONG_NEIGHBOR_FRACTION"] = str(args.strong_neighbor_fraction)
    os.environ["SORA_ENABLE_TWO_HOP"] = "0" if args.disable_two_hop else "1"
    os.environ["SORA_ADVERTISE_FUTURE_RESOURCE"] = "1" if args.advertise_future_resource else "0"
    # Advertisement ON automatically means direct one-hop neighbors consider
    # that future resource.  There is intentionally no independent Smart mode.
    os.environ["SORA_RESERVE_ANNOUNCED_FUTURE"] = "1" if args.advertise_future_resource else "0"
    os.environ["SORA_RS_MAX_ENTRIES"] = str(args.rs_max_entries)
    os.environ["SORA_RS_STATE_BITS"] = str(args.rs_state_bits)
    os.environ["SORA_RS_CE_HEADER_BYTES"] = str(args.rs_ce_header_bytes)
    os.environ["SORA_RS_HYBRID_META_BYTES"] = str(args.rs_hybrid_meta_bytes)
    os.environ["SORA_RS_WIRE_BYTES"] = str(int(rs_on_air["total_bytes"]) if rs_on_air else 0)
    os.environ["SORA_RS_LOGICAL_ENCODING"] = str(args.rs_logical_encoding)
    os.environ["SORA_RS_SERIALIZE"] = "1" if args.rs_serialize else "0"
    os.environ["SORA_ENABLE_COLLISION_FEEDBACK"] = "0" if args.disable_collision_feedback else "1"
    os.environ["SORA_ENABLE_FUTURE_RESOURCE"] = "0" if args.disable_future_resource else "1"
    os.environ["SORA_ENABLE_FUTURE_VERIFICATION"] = "0" if (args.disable_future_resource or args.disable_future_verification) else "1"
    os.environ["SORA_ENABLE_FUTURE_PROMOTION"] = "0" if (args.disable_future_resource or args.disable_future_promotion) else "1"
    os.environ["SORA_CANDIDATE_RULE"] = args.candidate_rule
    os.environ["SORA_MAP_CANDIDATE_FLOOR_PERCENT"] = str(args.map_candidate_floor_percent)
    os.environ["SORA_MAP_RELAX_STEP_DB"] = str(args.map_relax_step_db)
    os.environ["SORA_MAP_MAX_RELAXATIONS"] = str(args.map_max_relaxations)
    os.environ["SORA_RC_MODE"] = args.rc_mode
    os.environ["SORA_RC_FACTOR"] = str(args.rc_factor)
    # Common NR/PHY settings shared with protocol 0.
    os.environ["NRV2X_PROFILE"] = "lite" if args.profile == "legacy" else args.profile
    os.environ["NRV2X_SCENARIO"] = scenario
    os.environ["NRV2X_CHANNEL_SEED"] = str(args.seed)
    os.environ["NRV2X_RUN_SEED"] = str(args.seed)
    os.environ["NRV2X_RECEPTION_SEED"] = str(args.seed)
    os.environ["NRV2X_MCS_INDEX"] = str(args.mcs)
    os.environ["NRV2X_PACKET_BYTES"] = str(args.packet_bytes)
    os.environ["NRV2X_NUMEROLOGY"] = str(args.numerology)
    os.environ["NRV2X_RECEPTION_MODEL"] = args.reception_model
    os.environ["NRV2X_SL_PRIORITY"] = str(args.sl_priority)
    os.environ["NRV2X_PDB_S"] = str(args.pdb)
    os.environ["NRV2X_T2_S"] = str(args.t2)
    # RC is protocol-local: SORA uses SORA_RC_MODE/SORA_RC_FACTOR.
    os.environ["NRV2X_TX_POWER_MODE"] = args.tx_power_mode
    os.environ["NRV2X_NOISE_FIGURE_DB"] = str(args.noise_figure_db)
    os.environ["NRV2X_RECEIVER_SENSITIVITY_MODE"] = args.receiver_sensitivity_mode
    if args.receiver_power_gate_dbm is not None:
        os.environ["NRV2X_RECEIVER_POWER_GATE_DBM"] = str(args.receiver_power_gate_dbm)

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
    required_subchannels = sizing.required_subchannels if args.subchannels is None else int(args.subchannels)
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
        1, 20, True, 3,
        args.sensing_threshold_dbm, args.tx_power_dbm,
        -47.8, 2.3, sinr_threshold, str(output),
    )
    scheduler.run()

    metrics = summarize_run(output, nm)

    macs = [n.mac_layer for n in nm.nodes.values() if hasattr(n, "mac_layer")]
    rs_snapshots = sum(getattr(m, "sora_rs_snapshots", 0) for m in macs)
    rs_entries_total = sum(getattr(m, "sora_rs_entries_total", 0) for m in macs)
    rs_entries_max = max([getattr(m, "sora_rs_entries_max", 0) for m in macs] or [0])
    rs_resource_count = (
        macs[0].resource_allocation.total_slots
        * macs[0].resource_allocation.channels_per_slot
    ) if macs else 0
    rs_logical_bytes_total = sum(
        getattr(m, "sora_rs_logical_bytes_total", 0) for m in macs
    )
    rs_wire_bytes_total = sum(
        getattr(m, "sora_rs_wire_bytes_total", 0) for m in macs
    )
    rs_wire_tx = sum(getattr(m, "sora_rs_wire_tx", 0) for m in macs)
    encoding_names = sorted({
        name
        for m in macs
        for name in getattr(m, "sora_rs_encoding_bytes_total", {}).keys()
    })
    rs_encoding_bytes_total = {
        name: sum(int(getattr(m, "sora_rs_encoding_bytes_total", {}).get(name, 0)) for m in macs)
        for name in encoding_names
    }
    rs_encoding_packets = {
        name: sum(int(getattr(m, "sora_rs_encoding_packets", {}).get(name, 0)) for m in macs)
        for name in encoding_names
    }
    rs_encoding_mean_bytes = {
        name: (rs_encoding_bytes_total[name] / rs_encoding_packets[name] if rs_encoding_packets[name] else 0.0)
        for name in encoding_names
    }
    rs_preferred_bytes_total = sum(
        int(getattr(m, "sora_rs_preferred_bytes_total", 0)) for m in macs
    )
    rs_truncation_events = sum(
        getattr(m, "sora_rs_truncation_events", 0) for m in macs
    )
    rs_early_full_due_cap = sum(
        getattr(m, "sora_rs_early_full_due_cap", 0) for m in macs
    )
    overhead_ratio = overhead_percentages(
        application_bytes,
        int(rs_on_air["total_bytes"]) if rs_on_air else 0,
    )
    full_bitmap_reference = bitmap_rs_bytes(
        rri_s=args.rri,
        numerology=args.numerology,
        bandwidth_mhz=args.bandwidth_mhz,
        state_bits=args.rs_state_bits,
        ce_header_bytes=args.rs_ce_header_bytes,
        hybrid_meta_bytes=(
            args.rs_hybrid_meta_bytes if args.rs_mode == "hybrid" else 0
        ),
    )
    return {
        "profile": args.profile,
        "selector_profile": (args.profile if args.selector_profile == "auto" else args.selector_profile),
        "baseline_mode": "ARM6G" if getattr(args, "arm6g_mode", False) else "SORA",
        "phy_variant": args.phy_variant,
        "scenario": scenario,
        "carrier_frequency_ghz": args.carrier_frequency_ghz,
        "mobility": mobility_meta,
        "trace": trace_summary(vehicle_data),
        "mcs": args.mcs,
        "application_bytes": application_bytes,
        "packet_bytes": args.packet_bytes,
        "rs_on_air_mode": args.rs_on_air_mode,
        "rs_on_air": rs_on_air,
        "resource_sizing": sizing_mode,
        "required_prb": sizing.required_prb,
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
        "rc_mode": args.rc_mode,
        "tx_power_mode": args.tx_power_mode,
        "noise_figure_db": args.noise_figure_db,
        "receiver_sensitivity_mode": args.receiver_sensitivity_mode,
        "receiver_power_gate_dbm": args.receiver_power_gate_dbm,
        "rc_factor": args.rc_factor if args.rc_mode == "factor10" else 1.0,
        "collision_threshold": args.collision_threshold,
        "collision_mode": args.collision_mode,
        "strong_threshold": args.strong_threshold,
        "strong_neighbor_fraction": args.strong_neighbor_fraction,
        "two_hop": not args.disable_two_hop,
        "future_signaling": (
            "advertised_onehop" if args.advertise_future_resource else "private"
        ),
        "advertise_future_resource": bool(args.advertise_future_resource),
        "reserve_announced_future": bool(args.advertise_future_resource),
        "rs_max_entries": int(args.rs_max_entries),
        "rs_mode": args.rs_mode,
        "rs_full_interval_s": args.rs_full_interval,
        "rs_state_bits": int(args.rs_state_bits),
        "rs_ce_header_bytes": int(args.rs_ce_header_bytes),
        "rs_hybrid_meta_bytes": int(args.rs_hybrid_meta_bytes),
        "rs_physical_overhead": overhead_ratio,
        "rs_full_bitmap_reference": full_bitmap_reference,
        "sora_rs_full_tx": sum(getattr(m, "sora_rs_full_tx", 0) for m in macs),
        "sora_rs_delta_tx": sum(getattr(m, "sora_rs_delta_tx", 0) for m in macs),
        "sora_rs_invalid_delta_rx": sum(getattr(m, "sora_rs_invalid_delta_rx", 0) for m in macs),
        "sora_rs_truncation_events": rs_truncation_events,
        "sora_rs_early_full_due_cap": rs_early_full_due_cap,
        "sora_rs_logical_bytes_total": rs_logical_bytes_total,
        "sora_rs_mean_logical_bytes_per_tx": (
            rs_logical_bytes_total / rs_snapshots if rs_snapshots else 0.0
        ),
        "rs_logical_encoding_preference": args.rs_logical_encoding,
        "rs_serialization_enabled": bool(args.rs_serialize),
        "sora_rs_codec_decode_ok": sum(getattr(m, "sora_rs_codec_decode_ok", 0) for m in macs),
        "sora_rs_codec_decode_errors": sum(getattr(m, "sora_rs_codec_decode_errors", 0) for m in macs),
        "sora_rs_codec_bytes_total": sum(getattr(m, "sora_rs_codec_bytes_total", 0) for m in macs),
        "sora_rs_encoding_bytes_total": rs_encoding_bytes_total,
        "sora_rs_encoding_mean_bytes_per_packet": rs_encoding_mean_bytes,
        "sora_rs_preferred_encoding_bytes_total": rs_preferred_bytes_total,
        "sora_rs_preferred_encoding_mean_bytes_per_packet": (
            rs_preferred_bytes_total / rs_snapshots if rs_snapshots else 0.0
        ),
        "sora_rs_wire_bytes_total": rs_wire_bytes_total,
        "sora_rs_mean_wire_bytes_per_tx": (
            rs_wire_bytes_total / rs_wire_tx if rs_wire_tx else 0.0
        ),
        "collision_feedback": not args.disable_collision_feedback,
        "future_resource": not args.disable_future_resource,
        "future_verification": not (args.disable_future_resource or args.disable_future_verification),
        "future_promotion": not (args.disable_future_resource or args.disable_future_promotion),
        "candidate_rule": args.candidate_rule,
        "map_candidate_floor_percent": args.map_candidate_floor_percent,
        "map_relax_step_db": args.map_relax_step_db,
        "attempts": metrics["receiver_evaluations"],
        "success_pct": metrics["radio_eval_success_pct"],
        **metrics,
        "sora_feedback_events": sum(getattr(m, "sora_feedback_events", 0) for m in macs),
        "sora_collision_triggers": sum(getattr(m, "sora_collision_triggers", 0) for m in macs),
        "sora_future_reselections": sum(getattr(m, "sora_future_reselections", 0) for m in macs),
        "sora_fallback_selections": sum(getattr(m, "sora_fallback_selections", 0) for m in macs),
        "sora_nr_relaxation_events": sum(getattr(m, "sora_nr_relaxation_events", 0) for m in macs),
        "sora_nr_relaxations_total": sum(getattr(m, "sora_nr_relaxations_total", 0) for m in macs),
        "sora_map_relaxation_events": sum(getattr(m, "sora_map_relaxation_events", 0) for m in macs),
        "sora_map_relaxations_total": sum(getattr(m, "sora_map_relaxations_total", 0) for m in macs),
        "sora_rs_resource_count": rs_resource_count,
        "sora_rs_snapshots": rs_snapshots,
        "sora_rs_mean_nonfree_entries": (rs_entries_total / rs_snapshots if rs_snapshots else 0.0),
        "sora_rs_max_nonfree_entries": rs_entries_max,
        "output": str(output),
    }


def main():
    ap = argparse.ArgumentParser(description="Run reconstructed SORA on the frozen NR-V2X foundation")
    ap.add_argument("--profile", choices=["legacy", "core", "full"], default="full")
    ap.add_argument("--selector-profile", choices=["auto", "lite", "core", "core_no_reeval", "full"], default="auto",
                    help="Candidate-selector profile. Use --profile core --selector-profile lite for SORA collision logic with the Lite/simple selector/channel path.")
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
    ap.add_argument("--resample-trace", action="store_true")
    ap.add_argument("--mobility-dt", type=float, default=0.1)
    ap.add_argument("--vehicles", type=int, default=100)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mcs", type=int, default=11)
    ap.add_argument("--packet-bytes", type=int, default=150,
                    help="Total transmitted bytes unless --rs-on-air-mode fixed_cap is used")
    ap.add_argument("--carrier-frequency-ghz", type=float, default=5.89,
                    help="Carrier frequency used by the detailed V2V channel model")
    ap.add_argument("--application-bytes", type=int, default=None,
                    help="Application payload before SORA RS MAC-CE; used by fixed_cap mode")
    ap.add_argument("--rs-on-air-mode", choices=["none", "fixed_cap"], default="none",
                    help="fixed_cap: add a padded fixed-size sparse RS MAC-CE to the PHY packet size")
    ap.add_argument("--rs-state-bits", type=int, default=2)
    ap.add_argument("--rs-ce-header-bytes", type=int, default=2)
    ap.add_argument(
        "--rs-hybrid-meta-bytes", type=int, default=3,
        help="Simulation encoding budget for hybrid mode/epoch/sequence metadata",
    )
    ap.add_argument(
        "--rs-logical-encoding",
        choices=["rice_gap"],
        default="rice_gap",
        help="SORA RS wire encoding. Main implementation uses Golomb-Rice gap coding.",
    )
    ap.add_argument(
        "--no-rs-serialize", dest="rs_serialize", action="store_false",
        help="Disable the Golomb-Rice encode/decode wire path (debug only).",
    )
    ap.set_defaults(rs_serialize=True)
    ap.add_argument("--sinr-threshold", type=float, default=None)
    ap.add_argument("--reception-model", choices=["threshold", "logistic"], default="threshold")
    ap.add_argument("--sl-priority", type=int, choices=range(1, 9), default=1)
    ap.add_argument("--pdb", type=float, default=0.100)
    ap.add_argument("--t2", type=float, default=0.100)
    ap.add_argument("--bandwidth-mhz", type=int, default=10)
    ap.add_argument("--numerology", type=int, default=0)
    ap.add_argument("--rri", type=float, default=0.1)
    ap.add_argument("--resource-sizing", choices=["auto", "fixed", "legacy", "published"], default="auto")
    ap.add_argument("--subchannel-size-prb", type=int, default=10)
    ap.add_argument("--subchannels", type=int, default=None)
    ap.add_argument("--sensing-threshold-dbm", type=float, default=-90.0)
    ap.add_argument("--tx-power-dbm", type=float, default=23.0)
    ap.add_argument("--collision-threshold", type=float, default=0.8)
    ap.add_argument("--collision-mode", choices=["legacy_single", "paper_dual"], default="legacy_single")
    ap.add_argument("--strong-threshold", type=float, default=0.9)
    ap.add_argument("--strong-neighbor-fraction", type=float, default=0.5,
                    help="Closest fraction used for the paper-dual strong-neighbor subset")
    ap.add_argument("--min-feedback-reports", type=int, default=1)
    ap.add_argument("--arm6g-mode", action="store_true",
                    help="ARM6G paper baseline: RS-map/two-hop awareness, no future resource, no collision feedback/recovery, standard RC")
    ap.add_argument("--disable-two-hop", action="store_true")
    ap.add_argument(
        "--advertise-future-resource", dest="advertise_future_resource",
        action="store_true", default=True,
        help="Advertise SORA's prepared future resource to direct one-hop neighbors "
             "(DEFAULT: ON). Direct receivers avoid it when selecting their own future "
             "resource; it is never propagated as a two-hop RS state.",
    )
    ap.add_argument(
        "--no-advertise-future-resource", dest="advertise_future_resource",
        action="store_false",
        help="Disable future-resource advertisement and keep the prepared future private.",
    )
    ap.add_argument("--reserve-announced-future", action="store_true",
                    help=argparse.SUPPRESS)
    ap.add_argument("--rs-max-entries", type=int, default=0,
                    help="Cap sparse RS entries per packet; 0 keeps the uncapped research map")
    ap.add_argument("--disable-collision-feedback", action="store_true")
    ap.add_argument("--disable-future-resource", action="store_true",
                    help="Disable SORA prepared-future-resource creation, verification and promotion; useful for the no-future/ARM6G-style ablation")
    ap.add_argument("--disable-future-verification", action="store_true")
    ap.add_argument("--disable-future-promotion", action="store_true")
    ap.add_argument("--rs-expiry-rri", type=float, default=2.0)
    ap.add_argument("--rs-mode", choices=["snapshot", "hybrid"], default="hybrid",
                    help="hybrid uses periodic full-state plus delta updates with receiver reconstruction")
    ap.add_argument("--rs-full-interval", type=float, default=1.0,
                    help="Seconds between periodic full RS refreshes in hybrid mode")
    ap.add_argument("--candidate-rule", choices=["standard20", "map_only", "map5"], default="map5",
                    help="standard20: legacy pre-map NR 20%% floor; map_only: no candidate floor; map5: SORA-specific post-map floor (default 5%%) with +3 dB map relaxation")
    ap.add_argument("--map-candidate-floor-percent", type=float, default=5.0,
                    help="SORA post-map minimum safe-candidate percentage used by --candidate-rule map5")
    ap.add_argument("--map-relax-step-db", type=float, default=3.0,
                    help="Per-step map relaxation. +3 dB means -90 -> -87 dBm, reducing the exclusion distance")
    ap.add_argument("--map-max-relaxations", type=int, default=20)
    ap.add_argument("--rc-mode", choices=["factor10", "standard"], default="factor10",
                    help="SORA RC policy; factor10 preserves the historical SORA setting")
    ap.add_argument("--rc-factor", type=float, default=10.0,
                    help="Multiplier used when --rc-mode factor10 (default 10)")
    ap.add_argument("--tx-power-mode", choices=["total", "per_subchannel"], default="total",
                    help="total: 23 dBm is total UE power and is split across occupied subchannels; per_subchannel is diagnostic only")
    ap.add_argument("--noise-figure-db", type=float, default=9.0)
    ap.add_argument("--receiver-sensitivity-mode", choices=["implicit", "derived", "fixed"], default="implicit",
                    help="implicit: SINR threshold + thermal noise define sensitivity; derived: additionally gate at N+SINR threshold; fixed: use explicit dBm gate")
    ap.add_argument("--receiver-power-gate-dbm", type=float, default=None,
                    help="Used only with --receiver-sensitivity-mode fixed")
    ap.add_argument("--output", default="sora_results.csv")
    args = ap.parse_args()
    print(json.dumps(run(args), indent=2))


if __name__ == "__main__":
    main()
