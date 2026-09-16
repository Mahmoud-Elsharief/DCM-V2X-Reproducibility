from __future__ import annotations


def rc_bounds(rri_s: float, mode: str = "factor10", factor: float = 10.0) -> tuple[int, int]:
    mode = str(mode).strip().lower()
    if mode in ("factor10", "long", "legacy_factor10", "10x"):
        mult = float(factor)
    elif mode in ("standard", "normal", "matched", "1x"):
        mult = 1.0
    else:
        raise ValueError("mode must be standard or factor10")
    if rri_s * 1000 > 100:
        base_c = 1
    else:
        base_c = int(100 / max(20, rri_s * 1000))
    c = max(1.0, mult * base_c)
    return int(5*c), int(15*c)
