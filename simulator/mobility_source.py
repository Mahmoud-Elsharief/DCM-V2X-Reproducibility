from __future__ import annotations

from pathlib import Path
import importlib.util
import shutil
import tempfile

try:
    from .trace_io import read_sumo_vehicle_csv, resample_vehicle_data
    from .synthetic_mobility import generate_synthetic_mobility
    from .sumo_export_100ms import export_trace
except ImportError:  # direct script execution from integrated_sim/
    from trace_io import read_sumo_vehicle_csv, resample_vehicle_data
    from synthetic_mobility import generate_synthetic_mobility
    from sumo_export_100ms import export_trace


def sumo_runtime_available() -> bool:
    """Return whether both SUMO executable and Python tools appear available."""
    return (
        shutil.which("sumo") is not None
        and importlib.util.find_spec("traci") is not None
        and importlib.util.find_spec("sumolib") is not None
    )


def _load_trace(path: Path, *, resample_trace: bool, dt_s: float):
    data = read_sumo_vehicle_csv(str(path))
    if resample_trace:
        data = resample_vehicle_data(data, dt_s)
    return data


def _run_live_sumo(
    *,
    sumo_config: str,
    duration_s: float,
    dt_s: float,
    seed: int,
    begin_s: float = 0.0,
    gui: bool = False,
    cache_trace: str | None = None,
):
    cfg = Path(sumo_config)
    if not cfg.exists():
        raise FileNotFoundError(f"SUMO config not found: {sumo_config}")
    if not sumo_runtime_available():
        raise RuntimeError(
            "Live SUMO requested but SUMO/traci/sumolib are unavailable. "
            "Use --mobility-source trace or synthetic, or use auto fallback."
        )

    if cache_trace:
        out = Path(cache_trace)
        out.parent.mkdir(parents=True, exist_ok=True)
        temporary = False
    else:
        tmp = tempfile.NamedTemporaryFile(prefix="sora_live_sumo_", suffix=".csv", delete=False)
        tmp.close()
        out = Path(tmp.name)
        temporary = True

    export_trace(
        str(cfg), str(out),
        step_length=float(dt_s),
        begin=float(begin_s),
        end=float(begin_s) + float(duration_s),
        gui=bool(gui),
        seed=int(seed),
    )
    data = _load_trace(out, resample_trace=True, dt_s=dt_s)
    meta = {
        "source": "sumo",
        "sumo_config": str(cfg),
        "begin_s": float(begin_s),
        "duration_s": float(duration_s),
        "dt_s": float(dt_s),
        "seed": int(seed),
        "cached_trace": None if temporary else str(out),
    }
    if temporary:
        try:
            out.unlink()
        except OSError:
            pass
    return data, meta


def load_or_generate_mobility(
    *,
    scenario: str,
    trace_path: str | None = None,
    source: str = "auto",
    vehicles: int = 100,
    duration_s: float = 60.0,
    dt_s: float = 0.1,
    seed: int = 1,
    resample_trace: bool = False,
    sumo_config: str | None = None,
    sumo_begin_s: float = 0.0,
    sumo_gui: bool = False,
    sumo_cache_trace: str | None = None,
):
    """Return mobility from live SUMO, saved trace, or synthetic fallback.

    Modes
    -----
    ``sumo``
        Require SUMO + TraCI + ``sumo_config`` and generate the trace now.

    ``trace``
        Require ``trace_path`` and replay the saved mobility.

    ``synthetic``
        Generate reproducible Python mobility internally.

    ``auto``
        Prefer live SUMO when a config is supplied and the runtime is present;
        otherwise use an existing trace; otherwise generate synthetic mobility.

    The radio simulator therefore never has to stop merely because SUMO is not
    installed, unless the user explicitly requested ``source='sumo'``.
    """
    source = source.lower()
    if source not in {"auto", "sumo", "trace", "synthetic"}:
        raise ValueError("source must be auto, sumo, trace, or synthetic")

    path = Path(trace_path) if trace_path else None

    if source == "sumo":
        if not sumo_config:
            raise ValueError("source='sumo' requires sumo_config")
        return _run_live_sumo(
            sumo_config=sumo_config,
            duration_s=duration_s,
            dt_s=dt_s,
            seed=seed,
            begin_s=sumo_begin_s,
            gui=sumo_gui,
            cache_trace=sumo_cache_trace,
        )

    if source == "trace":
        if path is None or not path.exists():
            raise FileNotFoundError(f"Mobility trace not found: {trace_path}")
        data = _load_trace(path, resample_trace=resample_trace, dt_s=dt_s)
        return data, {"source": "trace", "path": str(path)}

    if source == "auto":
        if sumo_config and Path(sumo_config).exists() and sumo_runtime_available():
            return _run_live_sumo(
                sumo_config=sumo_config,
                duration_s=duration_s,
                dt_s=dt_s,
                seed=seed,
                begin_s=sumo_begin_s,
                gui=sumo_gui,
                cache_trace=sumo_cache_trace,
            )
        if path is not None and path.exists():
            data = _load_trace(path, resample_trace=resample_trace, dt_s=dt_s)
            return data, {
                "source": "trace",
                "path": str(path),
                "auto_fallback_reason": (
                    None if not sumo_config else "SUMO unavailable or config unusable"
                ),
            }

    data = generate_synthetic_mobility(
        scenario,
        vehicles=vehicles,
        duration_s=duration_s,
        dt_s=dt_s,
        seed=seed,
    )
    return data, {
        "source": "synthetic",
        "scenario": scenario,
        "seed": seed,
        "auto_fallback_reason": (
            "no usable SUMO runtime/config or saved trace" if source == "auto" else None
        ),
    }
