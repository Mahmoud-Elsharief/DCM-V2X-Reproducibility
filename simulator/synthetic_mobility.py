from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Literal


def _time_key(x):
    return round(float(x), 9)


@dataclass(frozen=True)
class HighwayMobilityConfig:
    vehicles: int = 100
    duration_s: float = 60.0
    dt_s: float = 0.1
    road_length_m: float = 4000.0
    lanes_per_direction: int = 3
    lane_width_m: float = 4.0
    mean_speed_mps: float = 30.0
    speed_std_mps: float = 2.5
    seed: int = 1


@dataclass(frozen=True)
class UrbanMobilityConfig:
    vehicles: int = 100
    duration_s: float = 60.0
    dt_s: float = 0.1
    blocks_x: int = 5
    blocks_y: int = 5
    block_length_m: float = 200.0
    lanes_per_direction: int = 1
    lane_width_m: float = 3.5
    mean_speed_mps: float = 12.0
    speed_std_mps: float = 1.5
    turn_probability: float = 0.25
    seed: int = 1


def generate_highway(cfg: HighwayMobilityConfig) -> dict:
    """Generate a reproducible straight multi-lane highway trace at ``dt_s``.

    Vehicles wrap around the finite road so density remains stationary.  Each
    direction receives a stable ``StreetID`` shared by all lanes; lane/edge IDs
    are retained separately for geometry/debugging.  This is a controlled
    fallback mobility model, not a replacement for SUMO car-following studies.
    """
    rng = random.Random(cfg.seed)
    data = {}
    total_lanes = 2 * cfg.lanes_per_direction
    steps = int(round(cfg.duration_s / cfg.dt_s)) + 1
    for i in range(cfg.vehicles):
        lane_index = i % total_lanes
        direction = 1 if lane_index < cfg.lanes_per_direction else -1
        local_lane = lane_index % cfg.lanes_per_direction
        y = (local_lane + 0.5) * cfg.lane_width_m
        if direction < 0:
            y = -y
        x0 = rng.uniform(0.0, cfg.road_length_m)
        speed = max(5.0, rng.gauss(cfg.mean_speed_mps, cfg.speed_std_mps))
        vid = f"h{i:04d}"
        records = []
        for k in range(steps):
            t = k * cfg.dt_s
            x = (x0 + direction * speed * t) % cfg.road_length_m
            heading = 90.0 if direction > 0 else 270.0  # SUMO convention
            records.append({
                "timestamp": _time_key(t),
                "position_x": x,
                "position_y": y,
                "speed": speed,
                "heading": heading,
                "street_id": "synthetic-highway-main",
                "road_id": f"hwy-dir-{direction:+d}",
                "edge_id": f"hwy-dir-{direction:+d}",
                "lane_id": f"hwy-{direction:+d}-lane-{local_lane}",
                "synthetic": True,
            })
        data[vid] = records
    return data


def _heading_from_vec(dx, dy):
    # SUMO angle: 0=north, 90=east, 180=south, 270=west.
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def generate_urban(cfg: UrbanMobilityConfig) -> dict:
    """Generate a Manhattan-grid fallback trace with road-aware metadata.

    Vehicles move along horizontal/vertical streets and make stochastic turns
    only at intersections.  ``StreetID`` is the physical corridor, independent
    of travel direction, allowing the urban channel to classify different
    streets as building-blocked NLOS without SUMO files.
    """
    rng = random.Random(cfg.seed)
    xmax = (cfg.blocks_x - 1) * cfg.block_length_m
    ymax = (cfg.blocks_y - 1) * cfg.block_length_m
    steps = int(round(cfg.duration_s / cfg.dt_s)) + 1
    data = {}

    def street_id(axis, idx):
        return f"urban-{axis}-{idx}"

    for i in range(cfg.vehicles):
        axis = rng.choice(["H", "V"])
        if axis == "H":
            street_idx = rng.randrange(cfg.blocks_y)
            y = street_idx * cfg.block_length_m
            x = rng.uniform(0.0, max(xmax, 1.0))
            direction = rng.choice([-1, 1])
        else:
            street_idx = rng.randrange(cfg.blocks_x)
            x = street_idx * cfg.block_length_m
            y = rng.uniform(0.0, max(ymax, 1.0))
            direction = rng.choice([-1, 1])
        speed = max(3.0, rng.gauss(cfg.mean_speed_mps, cfg.speed_std_mps))
        vid = f"u{i:04d}"
        records = []

        for k in range(steps):
            t = k * cfg.dt_s
            if axis == "H":
                dx, dy = direction, 0
                sid = street_id("H", street_idx)
            else:
                dx, dy = 0, direction
                sid = street_id("V", street_idx)

            records.append({
                "timestamp": _time_key(t),
                "position_x": x,
                "position_y": y,
                "speed": speed,
                "heading": _heading_from_vec(dx, dy),
                "street_id": sid,
                "road_id": f"{sid}-dir-{direction:+d}",
                "edge_id": f"{sid}-dir-{direction:+d}",
                "lane_id": f"{sid}-dir-{direction:+d}-lane-0",
                "synthetic": True,
            })

            remaining = speed * cfg.dt_s
            # Integrate potentially through more than one intersection.
            safety = 0
            while remaining > 1e-12 and safety < 8:
                safety += 1
                if axis == "H":
                    next_ix = (math.floor(x / cfg.block_length_m + 1e-9) + (1 if direction > 0 else 0)) * cfg.block_length_m
                    next_ix = min(max(next_ix, 0.0), xmax)
                    dist = abs(next_ix - x)
                    if dist < 1e-9:
                        # Boundary: force a turn if possible, otherwise reverse.
                        at_boundary = (x <= 1e-9 and direction < 0) or (x >= xmax - 1e-9 and direction > 0)
                    else:
                        at_boundary = False
                    if dist > remaining and not at_boundary:
                        x += direction * remaining
                        remaining = 0.0
                    else:
                        if not at_boundary:
                            x = next_ix; remaining -= dist
                        ix = int(round(x / cfg.block_length_m))
                        iy = int(round(y / cfg.block_length_m))
                        turn = at_boundary or (rng.random() < cfg.turn_probability)
                        if turn:
                            axis = "V"; street_idx = ix
                            valid_dirs = []
                            if y < ymax - 1e-9: valid_dirs.append(1)
                            if y > 1e-9: valid_dirs.append(-1)
                            direction = rng.choice(valid_dirs) if valid_dirs else -direction
                        elif at_boundary:
                            direction *= -1
                else:
                    next_iy = (math.floor(y / cfg.block_length_m + 1e-9) + (1 if direction > 0 else 0)) * cfg.block_length_m
                    next_iy = min(max(next_iy, 0.0), ymax)
                    dist = abs(next_iy - y)
                    if dist < 1e-9:
                        at_boundary = (y <= 1e-9 and direction < 0) or (y >= ymax - 1e-9 and direction > 0)
                    else:
                        at_boundary = False
                    if dist > remaining and not at_boundary:
                        y += direction * remaining
                        remaining = 0.0
                    else:
                        if not at_boundary:
                            y = next_iy; remaining -= dist
                        ix = int(round(x / cfg.block_length_m))
                        iy = int(round(y / cfg.block_length_m))
                        turn = at_boundary or (rng.random() < cfg.turn_probability)
                        if turn:
                            axis = "H"; street_idx = iy
                            valid_dirs = []
                            if x < xmax - 1e-9: valid_dirs.append(1)
                            if x > 1e-9: valid_dirs.append(-1)
                            direction = rng.choice(valid_dirs) if valid_dirs else -direction
                        elif at_boundary:
                            direction *= -1

        data[vid] = records
    return data


def generate_synthetic_mobility(
    scenario: Literal["highway", "urban"], *, vehicles=100, duration_s=60.0,
    dt_s=0.1, seed=1, **kwargs
) -> dict:
    scenario = str(scenario).lower()
    if scenario == "highway":
        return generate_highway(HighwayMobilityConfig(
            vehicles=vehicles, duration_s=duration_s, dt_s=dt_s, seed=seed, **kwargs
        ))
    if scenario in ("urban", "city"):
        return generate_urban(UrbanMobilityConfig(
            vehicles=vehicles, duration_s=duration_s, dt_s=dt_s, seed=seed, **kwargs
        ))
    raise ValueError(f"Unknown synthetic mobility scenario: {scenario}")
