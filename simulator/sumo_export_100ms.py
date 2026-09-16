"""Export a SUMO scenario at 100-ms resolution with road metadata.

This utility is intentionally independent of the SORA simulator.  Run it in an
environment where SUMO and its Python tools (traci, sumolib) are installed.
The resulting CSV can be fed directly to ``trace_io.read_sumo_vehicle_csv``.

Example
-------
python sumo_export_100ms.py --sumocfg city.sumocfg --output city_100ms.csv \
    --step-length 0.1 --end 300
"""
from __future__ import annotations

import argparse
import csv
import os


def export_trace(sumocfg: str, output: str, step_length: float = 0.1,
                 begin: float = 0.0, end: float | None = None,
                 gui: bool = False, seed: int | None = None):
    try:
        import traci
        from sumolib import checkBinary
    except ImportError as exc:
        raise RuntimeError(
            "SUMO Python tools are required. Install/configure SUMO so traci "
            "and sumolib are importable."
        ) from exc

    binary = checkBinary("sumo-gui" if gui else "sumo")
    cmd = [
        binary, "-c", os.path.abspath(sumocfg),
        "--step-length", str(float(step_length)),
        "--begin", str(float(begin)),
        "--no-step-log", "true",
    ]
    if end is not None:
        cmd += ["--end", str(float(end))]
    if seed is not None:
        cmd += ["--seed", str(int(seed))]

    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    fields = [
        "SimulationTime", "Step", "VehicleID", "PositionX", "PositionY",
        "Speed", "Heading", "StreetID", "EdgeID", "LaneID",
    ]

    def street_corridor_id(edge_id: str) -> str:
        """Return a direction-independent road-corridor identifier when possible.

        Priority: explicit SUMO street name; declared bidirectional edge pair;
        unordered from/to junction pair.  This avoids classifying the two travel
        directions of the same street as different-street building NLOS merely
        because their directed EdgeIDs differ.
        """
        if not edge_id or edge_id.startswith(":"):
            return ""
        try:
            name = traci.edge.getStreetName(edge_id)
            if name:
                return f"name:{name}"
        except Exception:
            pass
        try:
            bidi = traci.edge.getBidiEdge(edge_id)
            if bidi:
                a, b = sorted((edge_id, bidi))
                return f"bidi:{a}|{b}"
        except Exception:
            pass
        try:
            a = traci.edge.getFromJunction(edge_id)
            b = traci.edge.getToJunction(edge_id)
            if a and b:
                x, y = sorted((a, b))
                return f"junctions:{x}|{y}"
        except Exception:
            pass
        # Last-resort canonicalization covers the common SUMO convention where
        # a reverse edge is named with a leading '-'.
        return f"edge:{edge_id.lstrip('-')}"

    traci.start(cmd)
    try:
        with open(output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            step_index = 0
            while traci.simulation.getMinExpectedNumber() > 0:
                traci.simulationStep()
                now = float(traci.simulation.getTime())
                if end is not None and now > float(end) + 1e-9:
                    break
                for vehicle_id in sorted(traci.vehicle.getIDList()):
                    x, y = traci.vehicle.getPosition(vehicle_id)
                    edge_id = traci.vehicle.getRoadID(vehicle_id)
                    writer.writerow({
                        "SimulationTime": f"{now:.3f}",
                        # Use actual seconds as Step so the reconstructed
                        # simulator has no hidden unit conversion.
                        "Step": f"{now - float(begin):.3f}",
                        "VehicleID": vehicle_id,
                        "PositionX": f"{float(x):.6f}",
                        "PositionY": f"{float(y):.6f}",
                        "Speed": f"{float(traci.vehicle.getSpeed(vehicle_id)):.6f}",
                        "Heading": f"{float(traci.vehicle.getAngle(vehicle_id)):.6f}",
                        "StreetID": street_corridor_id(edge_id),
                        "EdgeID": edge_id,
                        "LaneID": traci.vehicle.getLaneID(vehicle_id),
                    })
                step_index += 1
    finally:
        traci.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sumocfg", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--step-length", type=float, default=0.1)
    ap.add_argument("--begin", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--gui", action="store_true")
    args = ap.parse_args()
    export_trace(
        args.sumocfg, args.output, args.step_length,
        args.begin, args.end, args.gui, args.seed,
    )


if __name__ == "__main__":
    main()
