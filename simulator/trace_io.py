from __future__ import annotations

from collections import defaultdict
import csv
import math
from typing import Iterable


def _time_key(value: float) -> float:
    """Stable route/scheduler key for sub-second mobility timestamps."""
    return round(float(value), 9)


def _shortest_heading_interp_deg(a: float, b: float, fraction: float) -> float:
    """Interpolate SUMO headings along the shortest circular arc."""
    a = float(a) % 360.0
    b = float(b) % 360.0
    delta = ((b - a + 180.0) % 360.0) - 180.0
    return (a + fraction * delta) % 360.0


def _linear(a: float, b: float, fraction: float) -> float:
    return float(a) + fraction * (float(b) - float(a))


def read_sumo_vehicle_csv(filename: str, start_step: float = 0.0):
    """Read a SORA/SUMO CSV while preserving channel-relevant metadata.

    The historical files use a one-second integer ``Step``.  Revised exports
    may use fractional seconds or an explicit ``SimulationTime``.  This loader
    keeps ``Heading`` and accepts optional road/edge/lane columns so the urban
    FULL channel can identify different-street building blockage directly.
    """
    vehicle_data = defaultdict(list)
    with open(filename, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            timestamp = float(row["Step"])
            if timestamp < float(start_step):
                continue
            item = {
                "timestamp": _time_key(timestamp - float(start_step)),
                "position_x": float(row["PositionX"]),
                "position_y": float(row["PositionY"]),
                "speed": float(row["Speed"]),
            }
            if row.get("Heading") not in (None, ""):
                item["heading"] = float(row["Heading"])
            for source, target in (
                ("RoadID", "road_id"), ("StreetID", "street_id"),
                ("EdgeID", "edge_id"), ("LaneID", "lane_id"),
            ):
                if row.get(source) not in (None, ""):
                    item[target] = row[source]
            vehicle_data[row["VehicleID"]].append(item)
    return dict(vehicle_data)


def infer_trace_dt(vehicle_data: dict, default: float = 1.0) -> float:
    """Return the smallest positive timestamp spacing found in a trace."""
    best = None
    for records in vehicle_data.values():
        times = sorted(float(r["timestamp"]) for r in records)
        for a, b in zip(times, times[1:]):
            d = b - a
            if d > 1e-9 and (best is None or d < best):
                best = d
    return float(default if best is None else round(best, 9))


def resample_vehicle_data(
    vehicle_data: dict,
    target_dt: float = 0.1,
    *,
    max_bridge_gap_s: float | None = None,
) -> dict:
    """Resample active vehicle trajectories to a finer deterministic grid.

    This is a **bridge for historical 1-s traces**, not a replacement for a
    native 100-ms SUMO export.  Between two consecutive observations it uses
    linear interpolation for x/y/speed and shortest-arc interpolation for
    heading.  Discrete road/edge/lane identifiers are held from the left-hand
    observation until the next observed transition, avoiding invented road
    identities.

    If ``max_bridge_gap_s`` is omitted it is set to 1.5 times the inferred
    native spacing, so a vehicle disappearance/reappearance is not bridged
    across a long missing interval.
    """
    target_dt = float(target_dt)
    if target_dt <= 0:
        raise ValueError("target_dt must be > 0")
    source_dt = infer_trace_dt(vehicle_data)
    if max_bridge_gap_s is None:
        max_bridge_gap_s = 1.5 * source_dt + 1e-9

    out = {}
    discrete_keys = ("road_id", "street_id", "edge_id", "lane_id")

    for vehicle_id, records in vehicle_data.items():
        records = sorted(records, key=lambda r: float(r["timestamp"]))
        if not records:
            continue
        generated = []
        seen_times = set()

        def append_record(item):
            key = _time_key(item["timestamp"])
            if key in seen_times:
                return
            copied = dict(item)
            copied["timestamp"] = key
            generated.append(copied)
            seen_times.add(key)

        if len(records) == 1:
            append_record(records[0])
            out[vehicle_id] = generated
            continue

        for left, right in zip(records, records[1:]):
            t0 = float(left["timestamp"])
            t1 = float(right["timestamp"])
            gap = t1 - t0
            if gap <= 1e-12:
                append_record(left)
                continue
            if gap > float(max_bridge_gap_s):
                append_record(left)
                continue

            # Integer tick construction avoids cumulative 0.1+0.1 drift.
            n = int(math.floor(gap / target_dt + 1e-9))
            for k in range(n):
                t = t0 + k * target_dt
                if t >= t1 - 1e-10:
                    break
                f = (t - t0) / gap
                item = {
                    "timestamp": _time_key(t),
                    "position_x": _linear(left["position_x"], right["position_x"], f),
                    "position_y": _linear(left["position_y"], right["position_y"], f),
                    "speed": _linear(left["speed"], right["speed"], f),
                    "interpolated": k != 0,
                }
                if "heading" in left and "heading" in right:
                    item["heading"] = _shortest_heading_interp_deg(
                        left["heading"], right["heading"], f
                    )
                elif "heading" in left:
                    item["heading"] = left["heading"]
                elif "heading" in right:
                    item["heading"] = right["heading"]
                for key in discrete_keys:
                    if key in left:
                        item[key] = left[key]
                append_record(item)

        append_record(records[-1])
        generated.sort(key=lambda r: float(r["timestamp"]))
        out[vehicle_id] = generated

    return out


def trace_summary(vehicle_data: dict) -> dict:
    timestamps = [float(r["timestamp"]) for rs in vehicle_data.values() for r in rs]
    return {
        "vehicles": len(vehicle_data),
        "records": sum(len(rs) for rs in vehicle_data.values()),
        "dt_s": infer_trace_dt(vehicle_data) if timestamps else None,
        "start_s": min(timestamps) if timestamps else None,
        "end_s": max(timestamps) if timestamps else None,
        "has_edge_id": any("edge_id" in r or "road_id" in r for rs in vehicle_data.values() for r in rs),
        "has_heading": any("heading" in r for rs in vehicle_data.values() for r in rs),
    }
