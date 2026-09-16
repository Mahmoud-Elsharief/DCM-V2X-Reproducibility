from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "paper_full_strict_mcs11.json"


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def bin_index(distance_m: float, bin_size_m: float, max_distance_m: float) -> int:
    n = int(math.ceil(max_distance_m / bin_size_m))
    i = int(math.ceil(distance_m / bin_size_m) - 1)
    return min(max(i, 0), n - 1)


def aggregate_receptions(csv_path: Path, *, warmup_s: float, duration_s: float, rri_s: float,
                         bin_size_m: float, max_distance_m: float) -> pd.DataFrame:
    required = ["timestamp", "sender_id", "receiver_id", "distance", "success"]
    header = pd.read_csv(csv_path, nrows=0)
    missing = [c for c in required if c not in header.columns]
    if missing:
        raise ValueError(f"{csv_path} missing columns {missing}; found {list(header.columns)}")

    nbin = int(math.ceil(max_distance_m / bin_size_m))
    trials = np.zeros(nbin, dtype=np.int64)
    successes = np.zeros(nbin, dtype=np.int64)
    pir_count = np.zeros(nbin, dtype=np.int64)
    pir_sum = np.zeros(nbin, dtype=np.float64)
    pir_sumsq = np.zeros(nbin, dtype=np.float64)
    last_success: Dict[Tuple[str, str], Tuple[float, float]] = {}
    reset_count = 0

    for ch in pd.read_csv(
        csv_path,
        usecols=required,
        dtype={"sender_id": "string", "receiver_id": "string"},
        chunksize=500_000,
    ):
        t = pd.to_numeric(ch["timestamp"], errors="coerce").to_numpy(float)
        d = pd.to_numeric(ch["distance"], errors="coerce").to_numpy(float)
        s = pd.to_numeric(ch["success"], errors="coerce").fillna(0).to_numpy(float)
        sender = ch["sender_id"].astype(str).to_numpy()
        receiver = ch["receiver_id"].astype(str).to_numpy()

        valid = np.isfinite(t) & np.isfinite(d) & (t >= warmup_s) & (t <= duration_s + 1e-9) & (d > 0.0)
        if not np.any(valid):
            continue

        for ti, di, si, tx, rx in zip(t[valid], d[valid], s[valid], sender[valid], receiver[valid]):
            pair = (tx, rx)
            if di > max_distance_m:
                if pair in last_success:
                    last_success.pop(pair, None)
                    reset_count += 1
                continue

            bi = bin_index(float(di), bin_size_m, max_distance_m)
            trials[bi] += 1
            if si <= 0:
                continue
            successes[bi] += 1

            previous = last_success.get(pair)
            if previous is not None:
                prev_t, prev_d = previous
                interval = float(ti) - float(prev_t)
                if interval > 0:
                    midpoint_d = 0.5 * (float(prev_d) + float(di))
                    if 0 < midpoint_d <= max_distance_m:
                        pi = bin_index(midpoint_d, bin_size_m, max_distance_m)
                        pir_count[pi] += 1
                        pir_sum[pi] += interval
                        pir_sumsq[pi] += interval * interval
            last_success[pair] = (float(ti), float(di))

    rows = []
    cum_success = 0
    cum_trials = 0
    cum_pir_sum = 0.0
    cum_pir_count = 0
    for i in range(nbin):
        lo, hi = i * bin_size_m, (i + 1) * bin_size_m
        n, k, pc = int(trials[i]), int(successes[i]), int(pir_count[i])
        prr = k / n if n else np.nan
        measured = float(pir_sum[i]) / pc if pc else np.nan
        formula = rri_s / prr if np.isfinite(prr) and prr > 0 else np.nan
        if pc > 1:
            var = (pir_sumsq[i] - pir_sum[i] ** 2 / pc) / (pc - 1)
            sd = math.sqrt(max(0.0, float(var)))
        else:
            sd = np.nan

        cum_success += k
        cum_trials += n
        cum_pir_sum += float(pir_sum[i])
        cum_pir_count += pc
        cum_prr = cum_success / cum_trials if cum_trials else np.nan
        cum_measured = cum_pir_sum / cum_pir_count if cum_pir_count else np.nan
        cum_formula = rri_s / cum_prr if np.isfinite(cum_prr) and cum_prr > 0 else np.nan

        rows.append({
            "bin_low_m": lo,
            "bin_high_m": hi,
            "bin_mid_m": 0.5 * (lo + hi),
            "successes": k,
            "trials": n,
            "PRR_bin": prr,
            "PIR_interval_count": pc,
            "PIR_measured_sum_s": float(pir_sum[i]),
            "PIR_measured_bin_s": measured,
            "PIR_measured_bin_ms": 1000.0 * measured if np.isfinite(measured) else np.nan,
            "PIR_measured_sd_s": sd,
            "PIR_formula_RRI_over_PRR_bin_s": formula,
            "cumulative_successes": cum_success,
            "cumulative_trials": cum_trials,
            "cumulative_PRR_pooled": cum_prr,
            "cumulative_PIR_interval_count": cum_pir_count,
            "cumulative_PIR_measured_sum_s": cum_pir_sum,
            "cumulative_PIR_measured_s": cum_measured,
            "cumulative_PIR_formula_s": cum_formula,
            "warmup_s": warmup_s,
            "measurement_window_s": duration_s - warmup_s,
            "pair_resets_outside_range_total": reset_count,
        })
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Aggregate one raw reception file into distance-binned PRR/PIR metrics.")
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = ap.parse_args()

    cfg = load_config(args.config)
    metric = aggregate_receptions(
        args.input,
        warmup_s=float(cfg["warmup_s"]),
        duration_s=float(cfg["duration_s"]),
        rri_s=float(cfg["rri_s"]),
        bin_size_m=float(cfg["distance_bin_m"]),
        max_distance_m=float(cfg["maximum_evaluation_distance_m"]),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    metric.to_csv(args.output, index=False)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
