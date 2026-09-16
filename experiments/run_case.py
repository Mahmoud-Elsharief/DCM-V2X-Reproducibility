from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / "simulator"
DEFAULT_CONFIG = ROOT / "configs" / "paper_full_strict_mcs11.json"
SCENARIO_FILE = ROOT / "configs" / "scenarios.json"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_command(protocol: str, scenario: str, density: str, seed: int, output: Path, cfg: dict) -> list[str]:
    scenario_map = load_json(SCENARIO_FILE)
    key = f"{scenario}_{density}"
    if key not in scenario_map:
        raise ValueError(f"Unsupported paper case: {key}")
    trace = ROOT / scenario_map[key]["trace"]
    if not trace.exists():
        raise FileNotFoundError(trace)

    script = SIM / ("run_sora.py" if protocol == "sora" else "run_nrv2x.py")
    cmd = [
        sys.executable, str(script),
        "--profile", str(cfg["profile"]),
        "--phy-variant", str(cfg["phy_variant"]),
        "--scenario", scenario,
        "--mobility-source", "trace",
        "--trace", str(trace),
        "--resample-trace",
        "--mobility-dt", str(cfg["mobility_dt_s"]),
        "--duration", str(cfg["duration_s"]),
        "--seed", str(seed),
        "--mcs", str(cfg["mcs"]),
        "--packet-bytes", str(cfg["sora_packet_bytes"] if protocol == "sora" else cfg["nrv2x_packet_bytes"]),
        "--carrier-frequency-ghz", str(cfg["carrier_frequency_ghz"]),
        "--bandwidth-mhz", str(cfg["bandwidth_mhz"]),
        "--numerology", str(cfg["numerology"]),
        "--rri", str(cfg["rri_s"]),
        "--resource-sizing", str(cfg["resource_sizing"]),
        "--sinr-threshold", str(cfg["sinr_threshold_db"]),
        "--sensing-threshold-dbm", str(cfg["sensing_threshold_dbm"]),
        "--tx-power-dbm", str(cfg["tx_power_dbm"]),
        "--noise-figure-db", str(cfg["noise_figure_db"]),
        "--tx-power-mode", str(cfg["tx_power_mode"]),
        "--receiver-sensitivity-mode", str(cfg["receiver_sensitivity_mode"]),
        "--reception-model", str(cfg["reception_model"]),
        "--output", str(output),
    ]

    if protocol == "nr":
        cmd += ["--rc-mode", str(cfg["nrv2x"]["rc_mode"])]
    else:
        s = cfg["sora"]
        cmd += [
            "--selector-profile", str(cfg["selector_profile"]),
            "--advertise-future-resource",
            "--application-bytes", str(cfg["application_bytes"]),
            "--rc-mode", str(s["rc_mode"]),
            "--rc-factor", str(s["rc_factor"]),
            "--candidate-rule", str(s["candidate_rule"]),
            "--map-candidate-floor-percent", str(s["map_candidate_floor_percent"]),
            "--map-relax-step-db", str(s["map_relax_step_db"]),
            "--collision-mode", str(s["collision_mode"]),
            "--collision-threshold", str(s["collision_threshold"]),
            "--strong-threshold", str(s["strong_threshold"]),
            "--strong-neighbor-fraction", str(s["strong_neighbor_fraction"]),
            "--rs-mode", str(s["rs_mode"]),
            "--rs-full-interval", str(s["rs_full_interval_s"]),
            "--rs-on-air-mode", str(s["rs_on_air_mode"]),
            "--rs-max-entries", str(s["rs_max_entries"]),
            "--rs-state-bits", str(s["rs_state_bits"]),
            "--rs-ce-header-bytes", str(s["rs_ce_header_bytes"]),
            "--rs-hybrid-meta-bytes", str(s["rs_hybrid_meta_bytes"]),
            "--rs-logical-encoding", str(s["rs_logical_encoding"]),
        ]
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run exactly one frozen paper case. This script intentionally does not batch the full paper matrix."
    )
    ap.add_argument("--protocol", choices=["sora", "nr"], required=True)
    ap.add_argument("--scenario", choices=["highway", "urban"], required=True)
    ap.add_argument("--density", choices=["low", "high"], required=True)
    ap.add_argument("--seed", type=int, default=None, help="If omitted, use the density seed frozen in the paper config.")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_json(args.config)
    seed = int(args.seed if args.seed is not None else cfg["default_seed_by_density"][args.density])
    case_dir = ROOT / "results" / "raw" / args.scenario / args.density / args.protocol / f"seed_{seed}"
    output = case_dir / "receptions.csv"
    log = case_dir / "run.log"
    command_file = case_dir / "command.txt"

    if output.exists() and not args.force and not args.dry_run:
        raise SystemExit(f"Output already exists: {output}\nUse --force only when you intentionally want to rerun this case.")

    cmd = build_command(args.protocol, args.scenario, args.density, seed, output, cfg)
    print(f"case={args.scenario}/{args.density}/{args.protocol}/seed_{seed}")
    print("command:")
    print(" ".join(map(str, cmd)))
    print(f"carrier_frequency_ghz={cfg['carrier_frequency_ghz']}")
    print(f"packet_bytes={cfg['sora_packet_bytes'] if args.protocol == 'sora' else cfg['nrv2x_packet_bytes']}")
    if args.dry_run:
        return 0

    case_dir.mkdir(parents=True, exist_ok=True)
    command_file.write_text(" ".join(map(str, cmd)) + "\n", encoding="utf-8")

    env = os.environ.copy()
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env.setdefault("NUMEXPR_NUM_THREADS", "1")
    # Also set the environment value for compatibility with lower-level channel
    # construction. The same value is present explicitly on the command line.
    env["NRV2X_FC_GHZ"] = str(cfg["carrier_frequency_ghz"])

    with log.open("w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=SIM, env=env, stdout=lf, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"Simulation failed. See {log}")
    if not output.exists() or output.stat().st_size == 0:
        raise SystemExit(f"Simulation returned success but produced no reception file: {output}")

    print(f"raw reception output: {output.relative_to(ROOT)}")
    print(f"run log: {log.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
