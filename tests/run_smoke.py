"""Short installation smoke test; this does NOT reproduce paper results."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / "simulator"
TRACE = ROOT / "tests" / "data" / "smoke_trace.csv"
OUT = ROOT / "results" / "smoke"

COMMON = [
    "--profile", "full", "--phy-variant", "strict", "--scenario", "highway",
    "--mobility-source", "trace", "--trace", str(TRACE), "--resample-trace",
    "--mobility-dt", "0.1", "--duration", "0.5", "--seed", "1",
    "--mcs", "11", "--carrier-frequency-ghz", "5.89", "--bandwidth-mhz", "10",
    "--numerology", "0", "--rri", "0.1", "--resource-sizing", "fixed",
    "--sinr-threshold", "10.145625", "--sensing-threshold-dbm", "-90",
    "--tx-power-dbm", "23", "--noise-figure-db", "9", "--tx-power-mode", "total",
    "--receiver-sensitivity-mode", "implicit", "--reception-model", "threshold",
]


def run(protocol: str):
    OUT.mkdir(parents=True, exist_ok=True)
    output = OUT / f"{protocol}.csv"
    script = SIM / ("run_sora.py" if protocol == "sora" else "run_nrv2x.py")
    packet_bytes = "349" if protocol == "sora" else "300"
    cmd = [sys.executable, str(script)] + COMMON + ["--packet-bytes", packet_bytes]
    if protocol == "nr":
        cmd += ["--rc-mode", "standard"]
    else:
        cmd += [
            "--selector-profile", "full", "--advertise-future-resource",
            "--application-bytes", "300", "--rc-mode", "factor10",
            "--candidate-rule", "map5", "--map-candidate-floor-percent", "5",
            "--map-relax-step-db", "3", "--collision-mode", "paper_dual",
            "--collision-threshold", "0.8", "--strong-threshold", "0.9",
            "--strong-neighbor-fraction", "0.5", "--rs-mode", "hybrid",
            "--rs-full-interval", "1.0", "--rs-on-air-mode", "none",
            "--rs-max-entries", "0", "--rs-logical-encoding", "rice_gap",
        ]
    cmd += ["--output", str(output)]
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "1"
    env["NRV2X_FC_GHZ"] = "5.89"
    subprocess.run(cmd, cwd=SIM, env=env, check=True, stdout=subprocess.DEVNULL)
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Smoke test produced no output for {protocol}")


if __name__ == "__main__":
    run("nr")
    run("sora")
    print("Smoke test passed for NR-V2X and SORA.")
    print("This smoke test is only an installation check and does not reproduce paper results.")
