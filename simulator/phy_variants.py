from __future__ import annotations

import os

PHY_VARIANTS = {
    "strict": {
        "NRV2X_SHADOWING": "1",
        "NRV2X_CORRELATED_SHADOWING": "1",
        "NRV2X_NLOSV_BLOCKAGE": "1",
        "NRV2X_RELAXED_SCI": "0",
        "NRV2X_INFER_STREET_FROM_HEADING": "1",
        "NRV2X_STREET_HEADING_TOLERANCE_DEG": "25.0",
        "NRV2X_STREET_CORRIDOR_HALF_WIDTH_M": "20.0",
        "NRV2X_URBAN_NLOS_RELAX_DB": "0.0",
    },
    # Refined Full-Relaxed used for highway sensitivity: shadowing stays on,
    # vehicle blockage is removed, and SCI acquisition is made robust.
    "relaxed": {
        "NRV2X_SHADOWING": "1",
        "NRV2X_CORRELATED_SHADOWING": "1",
        "NRV2X_NLOSV_BLOCKAGE": "0",
        "NRV2X_RELAXED_SCI": "1",
        "NRV2X_INFER_STREET_FROM_HEADING": "1",
        "NRV2X_STREET_HEADING_TOLERANCE_DEG": "25.0",
        "NRV2X_STREET_CORRIDOR_HALF_WIDTH_M": "20.0",
        "NRV2X_URBAN_NLOS_RELAX_DB": "0.0",
    },
    "urban_relaxed1": {
        "NRV2X_SHADOWING": "1",
        "NRV2X_CORRELATED_SHADOWING": "1",
        "NRV2X_NLOSV_BLOCKAGE": "1",
        "NRV2X_RELAXED_SCI": "1",
        "NRV2X_INFER_STREET_FROM_HEADING": "1",
        "NRV2X_STREET_HEADING_TOLERANCE_DEG": "35.0",
        "NRV2X_STREET_CORRIDOR_HALF_WIDTH_M": "30.0",
        "NRV2X_URBAN_NLOS_RELAX_DB": "3.0",
    },
    # Recommended urban sensitivity profile from V0.12.
    "urban_relaxed2": {
        "NRV2X_SHADOWING": "1",
        "NRV2X_CORRELATED_SHADOWING": "1",
        "NRV2X_NLOSV_BLOCKAGE": "1",
        "NRV2X_RELAXED_SCI": "1",
        "NRV2X_INFER_STREET_FROM_HEADING": "1",
        "NRV2X_STREET_HEADING_TOLERANCE_DEG": "45.0",
        "NRV2X_STREET_CORRIDOR_HALF_WIDTH_M": "40.0",
        "NRV2X_URBAN_NLOS_RELAX_DB": "6.0",
    },
}


def apply_phy_variant(name: str) -> dict[str, str]:
    try:
        values = PHY_VARIANTS[str(name)]
    except KeyError as exc:
        raise ValueError(f"Unknown PHY variant {name!r}; choose one of {sorted(PHY_VARIANTS)}") from exc
    for key, value in values.items():
        os.environ[key] = value
    os.environ["NRV2X_PHY_VARIANT"] = str(name)
    return dict(values)
