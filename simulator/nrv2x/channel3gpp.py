from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import math
import random
from typing import Hashable, Iterable


class V2VScenario(str, Enum):
    HIGHWAY = "highway"
    URBAN = "urban"


class LinkState(str, Enum):
    LOS = "LOS"
    NLOSV = "NLOSv"
    NLOS = "NLOS"


@dataclass(frozen=True)
class V2VChannelConfig:
    """Large-scale V2V channel abstraction aligned with 3GPP TR 37.885.

    The model implements the TR 37.885 LOS/NLOSv probabilities, the highway
    and urban V2V path-loss equations, NLOS building path loss, log-normal
    shadow fading standard deviations, and the stochastic NLOSv blockage
    option.  It intentionally does not implement the full tapped-delay-line
    small-scale model; that is kept as a separate later validation stage.

    ``urban_different_street_is_nlos`` only takes effect when both nodes expose
    a non-None ``road_id``/``street_id`` attribute.  With no street metadata an
    urban link is treated as a same-street LOS/NLOSv link rather than inventing
    building geometry.
    """

    scenario: V2VScenario = V2VScenario.HIGHWAY
    carrier_frequency_ghz: float = 5.89
    seed: int = 1
    state_update_s: float = 1.0
    shadowing: bool = True
    correlated_shadowing: bool = True
    nlosv_blockage: bool = True
    urban_different_street_is_nlos: bool = True
    default_antenna_height_m: float = 1.6
    # Optional geometry fallback for SUMO traces that contain Heading but no
    # edge/road id.  This only classifies whether two UEs are on the same road
    # corridor; it is not a propagation equation.
    infer_street_from_heading: bool = True
    street_heading_tolerance_deg: float = 25.0
    street_corridor_half_width_m: float = 20.0
    # Controlled urban sensitivity: positive value reduces only building-NLOS
    # path loss. LOS/NLOSv equations, shadowing, noise, and MCS thresholds stay unchanged.
    urban_nlos_relax_db: float = 0.0
    # TR 37.885 Table 8: shadow-fading correlation distance is 10 m for LOS
    # and 13 m for NLOS/NLOSv in the V2V urban/highway parameterization.
    shadow_corr_distance_los_m: float = 10.0
    shadow_corr_distance_other_m: float = 13.0


@dataclass(frozen=True)
class LinkBudget:
    state: LinkState
    distance_m: float
    path_loss_db: float
    shadow_fading_db: float
    blockage_loss_db: float
    total_loss_db: float
    received_power_dbm: float


@dataclass
class _StateMemory:
    state: LinkState
    last_update_s: float


@dataclass
class _ShadowMemory:
    value_db: float
    tx_position: tuple[float, float]
    rx_position: tuple[float, float]
    state: LinkState


def _stable_seed(base_seed: int, *parts: object) -> int:
    text = "|".join(str(x) for x in (base_seed,) + parts)
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _distance_2d(a: Iterable[float], b: Iterable[float]) -> float:
    aa = tuple(a)
    bb = tuple(b)
    return math.hypot(float(aa[0]) - float(bb[0]), float(aa[1]) - float(bb[1]))


class V2VLargeScaleChannel:
    """Time-consistent system-level V2V large-scale channel.

    State and shadow memories are keyed by an unordered link key so that the
    large-scale component is reciprocal for A<->B within the same simulator.
    """

    def __init__(self, cfg: V2VChannelConfig):
        self.cfg = cfg
        self._state: dict[tuple[str, str], _StateMemory] = {}
        self._shadow: dict[tuple[str, str], _ShadowMemory] = {}

    @staticmethod
    def _node_id(node: object) -> str:
        return str(getattr(node, "node_id", id(node)))

    def _key(self, tx: object, rx: object) -> tuple[str, str]:
        a, b = self._node_id(tx), self._node_id(rx)
        return (a, b) if a <= b else (b, a)

    @staticmethod
    def _position(node: object) -> tuple[float, float]:
        pos = getattr(node, "position")
        return (float(pos[0]), float(pos[1]))

    def _canonical_positions(self, tx: object, rx: object) -> tuple[tuple[float, float], tuple[float, float]]:
        tx_id, rx_id = self._node_id(tx), self._node_id(rx)
        txp, rxp = self._position(tx), self._position(rx)
        return (txp, rxp) if tx_id <= rx_id else (rxp, txp)

    @staticmethod
    def _street_id(node: object):
        for name in ("road_id", "street_id", "edge_id"):
            value = getattr(node, name, None)
            if value is not None:
                return value
        return None

    @staticmethod
    def highway_los_probability(distance_m: float) -> float:
        d = max(0.0, float(distance_m))
        if d <= 475.0:
            p = 2.1013e-6 * d * d - 0.002 * d + 1.0193
            return min(1.0, max(0.0, p))
        return max(0.0, min(1.0, 0.54 - 0.001 * (d - 475.0)))

    @staticmethod
    def urban_los_probability(distance_m: float) -> float:
        d = max(0.0, float(distance_m))
        return min(1.0, max(0.0, 1.05 * math.exp(-0.0114 * d)))

    @staticmethod
    def _heading_deg(node: object):
        value = getattr(node, "heading", None)
        return None if value is None else float(value) % 360.0

    @staticmethod
    def _axis_angle_difference_deg(a: float, b: float) -> float:
        # Same street includes opposite travel directions, so heading is an
        # undirected axis modulo 180 degrees.
        diff = abs((a - b) % 180.0)
        return min(diff, 180.0 - diff)

    @staticmethod
    def _sumo_heading_unit(heading_deg: float) -> tuple[float, float]:
        # SUMO angles: 0 deg=north, 90 deg=east.
        theta = math.radians(heading_deg)
        return (math.sin(theta), math.cos(theta))

    def _same_street(self, tx: object, rx: object) -> bool | None:
        a = self._street_id(tx)
        b = self._street_id(rx)
        if a is not None and b is not None:
            return a == b

        if not self.cfg.infer_street_from_heading:
            return None
        ha = self._heading_deg(tx)
        hb = self._heading_deg(rx)
        if ha is None or hb is None:
            return None
        if self._axis_angle_difference_deg(ha, hb) > self.cfg.street_heading_tolerance_deg:
            return False

        pa, pb = self._position(tx), self._position(rx)
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        ua = self._sumo_heading_unit(ha)
        ub = self._sumo_heading_unit(hb)
        # Perpendicular distance to each vehicle's local road tangent.
        lateral_a = abs(dx * ua[1] - dy * ua[0])
        lateral_b = abs(dx * ub[1] - dy * ub[0])
        return max(lateral_a, lateral_b) <= self.cfg.street_corridor_half_width_m

    def _draw_state(self, tx: object, rx: object, now_s: float, distance_m: float) -> LinkState:
        if self.cfg.scenario is V2VScenario.URBAN and self.cfg.urban_different_street_is_nlos:
            same_street = self._same_street(tx, rx)
            if same_street is False:
                return LinkState.NLOS

        p_los = (
            self.highway_los_probability(distance_m)
            if self.cfg.scenario is V2VScenario.HIGHWAY
            else self.urban_los_probability(distance_m)
        )
        key = self._key(tx, rx)
        bucket = int(math.floor(now_s / max(self.cfg.state_update_s, 1e-9)))
        rng = random.Random(_stable_seed(self.cfg.seed, "state", key, bucket))
        return LinkState.LOS if rng.random() <= p_los else LinkState.NLOSV

    def state(self, tx: object, rx: object, now_s: float) -> LinkState:
        key = self._key(tx, rx)
        d = _distance_2d(self._position(tx), self._position(rx))
        mem = self._state.get(key)

        # A change to/from a different urban street must be reflected without
        # waiting for the LOS/NLOSv refresh period.
        if self.cfg.scenario is V2VScenario.URBAN and self.cfg.urban_different_street_is_nlos:
            same_street = self._same_street(tx, rx)
            if same_street is False:
                state = LinkState.NLOS
                self._state[key] = _StateMemory(state, now_s)
                return state
            if same_street is True and mem is not None and mem.state is LinkState.NLOS:
                mem = None

        if mem is None or now_s - mem.last_update_s >= self.cfg.state_update_s - 1e-12:
            state = self._draw_state(tx, rx, now_s, d)
            self._state[key] = _StateMemory(state, now_s)
            return state
        return mem.state

    def path_loss_db(self, distance_m: float, state: LinkState) -> float:
        d = max(float(distance_m), 1.0)
        fc = float(self.cfg.carrier_frequency_ghz)
        if state is LinkState.NLOS:
            base = 36.85 + 30.0 * math.log10(d) + 18.9 * math.log10(fc)
            return base - max(0.0, float(self.cfg.urban_nlos_relax_db))
        if self.cfg.scenario is V2VScenario.HIGHWAY:
            return 32.4 + 20.0 * math.log10(d) + 20.0 * math.log10(fc)
        return 38.77 + 16.7 * math.log10(d) + 18.2 * math.log10(fc)

    def shadow_sigma_db(self, state: LinkState) -> float:
        if self.cfg.scenario is V2VScenario.HIGHWAY:
            return 3.3 if state is LinkState.LOS else 3.8
        if state is LinkState.LOS:
            return 5.2
        if state is LinkState.NLOSV:
            return 5.3
        return 6.8

    def _shadow_corr_distance(self, state: LinkState) -> float:
        if state is LinkState.LOS:
            return self.cfg.shadow_corr_distance_los_m
        return self.cfg.shadow_corr_distance_other_m

    def shadow_fading_db(self, tx: object, rx: object, now_s: float, state: LinkState) -> float:
        if not self.cfg.shadowing:
            return 0.0
        key = self._key(tx, rx)
        txp, rxp = self._canonical_positions(tx, rx)
        sigma = self.shadow_sigma_db(state)
        mem = self._shadow.get(key)

        if mem is None or mem.state is not state or not self.cfg.correlated_shadowing:
            bucket = int(math.floor(now_s / max(self.cfg.state_update_s, 1e-9)))
            rng = random.Random(_stable_seed(self.cfg.seed, "shadow", key, state.value, bucket))
            value = rng.gauss(0.0, sigma)
            self._shadow[key] = _ShadowMemory(value, txp, rxp, state)
            return value

        moved_tx = _distance_2d(txp, mem.tx_position)
        moved_rx = _distance_2d(rxp, mem.rx_position)
        delta = 0.5 * (moved_tx + moved_rx)
        if delta <= 1e-12:
            return mem.value_db

        corr_d = max(self._shadow_corr_distance(state), 1e-9)
        rho = math.exp(-delta / corr_d)
        # Deterministic innovation tied to link, state and quantized current
        # position, avoiding dependence on dictionary/process iteration order.
        quant = tuple(round(x, 3) for x in (*txp, *rxp))
        rng = random.Random(_stable_seed(self.cfg.seed, "shadow-step", key, state.value, quant))
        innovation = rng.gauss(0.0, sigma)
        value = rho * mem.value_db + math.sqrt(max(0.0, 1.0 - rho * rho)) * innovation
        self._shadow[key] = _ShadowMemory(value, txp, rxp, state)
        return value

    def nlosv_blockage_loss_db(self, tx: object, rx: object, now_s: float, distance_m: float, state: LinkState) -> float:
        if state is not LinkState.NLOSV or not self.cfg.nlosv_blockage:
            return 0.0
        d = max(float(distance_m), 1.0)
        # TR 37.885 stochastic blockage Option 2, Case 3 (typical passenger
        # vehicles with TX/RX antennas around blocker height):
        # mean = 5 + max(0, 15 log10(d)-41), std = 4 dB, clipped at 0 dB.
        mean = 5.0 + max(0.0, 15.0 * math.log10(d) - 41.0)
        key = self._key(tx, rx)
        bucket = int(math.floor(now_s / max(self.cfg.state_update_s, 1e-9)))
        rng = random.Random(_stable_seed(self.cfg.seed, "nlosv", key, bucket))
        return max(0.0, rng.gauss(mean, 4.0))

    def link_budget(self, tx: object, rx: object, now_s: float, tx_power_dbm: float) -> LinkBudget:
        d = _distance_2d(self._position(tx), self._position(rx))
        state = self.state(tx, rx, now_s)
        pl = self.path_loss_db(d, state)
        sf = self.shadow_fading_db(tx, rx, now_s, state)
        blk = self.nlosv_blockage_loss_db(tx, rx, now_s, d, state)
        total = pl + sf + blk
        return LinkBudget(
            state=state,
            distance_m=d,
            path_loss_db=pl,
            shadow_fading_db=sf,
            blockage_loss_db=blk,
            total_loss_db=total,
            received_power_dbm=float(tx_power_dbm) - total,
        )
