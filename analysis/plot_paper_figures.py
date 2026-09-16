from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "paper_full_strict_mcs11.json"

plt.rcParams.update({
    "font.size": 15,
    "axes.labelsize": 17,
    "axes.titlesize": 16,
    "legend.fontsize": 11,
    "xtick.labelsize": 13,
    "ytick.labelsize": 13,
    "lines.linewidth": 2.4,
    "lines.markersize": 5,
    "figure.figsize": (8.7, 5.6),
    "grid.linestyle": "--",
    "axes.grid": True,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "savefig.dpi": 300,
})


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_pooled_metrics(metrics_root: Path, scenario: str, density: str, protocol: str, rri_s: float) -> pd.DataFrame | None:
    files = sorted((metrics_root / scenario / density / protocol).glob("seed_*/prr_pir.csv"))
    if not files:
        return None
    frames = [pd.read_csv(f) for f in files]
    base = frames[0][["bin_low_m", "bin_high_m", "bin_mid_m"]].copy()
    successes = sum(f["successes"].to_numpy(float) for f in frames)
    trials = sum(f["trials"].to_numpy(float) for f in frames)
    pir_count = sum(f["PIR_interval_count"].to_numpy(float) for f in frames)
    pir_sum = sum(f["PIR_measured_sum_s"].to_numpy(float) for f in frames)

    cum_s = np.cumsum(successes)
    cum_t = np.cumsum(trials)
    cum_pc = np.cumsum(pir_count)
    cum_ps = np.cumsum(pir_sum)
    base["cumulative_PRR_pooled"] = np.divide(cum_s, cum_t, out=np.full_like(cum_s, np.nan), where=cum_t > 0)
    base["cumulative_PIR_measured_s"] = np.divide(cum_ps, cum_pc, out=np.full_like(cum_ps, np.nan), where=cum_pc > 0)
    base["cumulative_PIR_formula_s"] = np.divide(
        rri_s,
        base["cumulative_PRR_pooled"].to_numpy(float),
        out=np.full(len(base), np.nan),
        where=base["cumulative_PRR_pooled"].to_numpy(float) > 0,
    )
    base["seed_count"] = len(files)
    return base


def save(fig, stem: Path):
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(stem.with_suffix(".png"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the paper-style PRR/PIR figures from already aggregated metric files.")
    ap.add_argument("--metrics-root", type=Path, default=ROOT / "results" / "metrics")
    ap.add_argument("--output", type=Path, default=ROOT / "figures")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = ap.parse_args()
    cfg = load_config(args.config)
    rri = float(cfg["rri_s"])
    max_d = float(cfg["maximum_evaluation_distance_m"])

    order = [
        ("sora", "high", "SORA-H"),
        ("sora", "low", "SORA-L"),
        ("nr", "high", "NRV2X-H"),
        ("nr", "low", "NRV2X-L"),
    ]

    missing = []
    output_names = {
        ("highway", "prr"): "Fig18_highway_prr",
        ("highway", "pir"): "Fig19_highway_pir",
        ("urban", "prr"): "Fig20_urban_prr",
        ("urban", "pir"): "Fig21_urban_pir",
    }
    axis_ranges = {
        ("highway", "prr"): (0.78, 1.005),
        ("highway", "pir"): (0.099, 0.122),
        ("urban", "prr"): (0.35, 1.005),
        ("urban", "pir"): (0.099, 0.142),
    }

    for scenario in ("highway", "urban"):
        curves = {}
        for proto, density, _ in order:
            g = load_pooled_metrics(args.metrics_root, scenario, density, proto, rri)
            if g is None:
                missing.append(f"{scenario}/{density}/{proto}")
            else:
                curves[(proto, density)] = g

        if not curves:
            continue

        fig, ax = plt.subplots()
        for proto, density, label in order:
            g = curves.get((proto, density))
            if g is not None:
                ax.plot(g["bin_high_m"], g["cumulative_PRR_pooled"], marker="x", label=label)
        ax.set_xlabel("Distance (m)")
        ax.set_ylabel("PRR")
        ax.set_xlim(0, max_d)
        ax.set_ylim(*axis_ranges[(scenario, "prr")])
        ax.legend()
        save(fig, args.output / output_names[(scenario, "prr")])

        fig, ax = plt.subplots()
        for proto, density, label in order:
            g = curves.get((proto, density))
            if g is not None:
                ax.plot(g["bin_high_m"], g["cumulative_PIR_measured_s"], marker="x", label=label)
        ax.set_xlabel("Distance (m)")
        ax.set_ylabel("PIR (s)")
        ax.set_xlim(0, max_d)
        ax.set_ylim(*axis_ranges[(scenario, "pir")])
        ax.legend()
        save(fig, args.output / output_names[(scenario, "pir")])

    if missing:
        print("Warning: figures were generated from the available cases. Missing metrics:")
        for item in missing:
            print("  -", item)
    print("Figures:", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
