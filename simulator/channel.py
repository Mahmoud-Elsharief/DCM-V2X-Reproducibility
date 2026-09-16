import logging
import math
import os

from nrv2x import V2VChannelConfig, V2VLargeScaleChannel, V2VScenario

logger = logging.getLogger(__name__)


def _dbm_to_mw(value_dbm: float) -> float:
    return 10.0 ** (value_dbm / 10.0)


def _mw_to_dbm(value_mw: float) -> float:
    return 10.0 * math.log10(max(value_mw, 1e-300))


class ChannelModel:
    """Common broadcast/channel layer for the reconstruction.

    LITE/CORE preserve the supplied legacy propagation path in MACLayer.
    FULL calls this object's 3GPP-style V2V large-scale link budget.  Keeping
    the channel object common is deliberate: SORA can later use the *same*
    propagation/reception machinery without copying any PHY code.
    """

    def __init__(self, scenario=None, carrier_frequency_ghz=None, seed=None,
                 noise_figure_db=None):
        self.nodes = []
        scenario_name = (scenario or os.environ.get("NRV2X_SCENARIO", "highway")).strip().lower()
        if scenario_name not in ("highway", "urban"):
            raise ValueError(f"Unsupported NRV2X_SCENARIO={scenario_name!r}")
        self.scenario = V2VScenario(scenario_name)
        self.carrier_frequency_ghz = float(
            carrier_frequency_ghz if carrier_frequency_ghz is not None
            else os.environ.get("NRV2X_FC_GHZ", "5.89")
        )
        self.seed = int(seed if seed is not None else os.environ.get("NRV2X_CHANNEL_SEED", "1"))
        self.noise_figure_db = float(
            noise_figure_db if noise_figure_db is not None
            else os.environ.get("NRV2X_NOISE_FIGURE_DB", "9.0")
        )
        self.propagation = V2VLargeScaleChannel(V2VChannelConfig(
            scenario=self.scenario,
            carrier_frequency_ghz=self.carrier_frequency_ghz,
            seed=self.seed,
            state_update_s=float(os.environ.get("NRV2X_STATE_UPDATE_S", "1.0")),
            shadowing=os.environ.get("NRV2X_SHADOWING", "1") != "0",
            correlated_shadowing=os.environ.get("NRV2X_CORRELATED_SHADOWING", "1") != "0",
            nlosv_blockage=os.environ.get("NRV2X_NLOSV_BLOCKAGE", "1") != "0",
            infer_street_from_heading=os.environ.get("NRV2X_INFER_STREET_FROM_HEADING", "1") != "0",
            street_heading_tolerance_deg=float(os.environ.get("NRV2X_STREET_HEADING_TOLERANCE_DEG", "25.0")),
            street_corridor_half_width_m=float(os.environ.get("NRV2X_STREET_CORRIDOR_HALF_WIDTH_M", "20.0")),
            urban_nlos_relax_db=float(os.environ.get("NRV2X_URBAN_NLOS_RELAX_DB", "0.0")),
        ))

    def register_node(self, node):
        if node not in self.nodes:
            self.nodes.append(node)

    def unregister_node(self, node):
        try:
            self.nodes.remove(node)
        except ValueError:
            pass

    def transmit(self, sender, packet, resources):
        _range_m = float(os.environ.get("SORA_COMM_RANGE_M", "inf"))
        for node in tuple(self.nodes):
            if getattr(node, "stopnode", False):
                continue
            if node == sender:
                continue
            if math.isfinite(_range_m):
                try:
                    dx = float(node.position[0]) - float(sender.position[0])
                    dy = float(node.position[1]) - float(sender.position[1])
                    if math.hypot(dx, dy) > _range_m:
                        continue
                except Exception:
                    pass
            mac = getattr(node, "mac_layer", None)
            if mac is not None and getattr(mac, "common_core_enabled", lambda: False)():
                # A UE scheduled to transmit at this exact time cannot receive
                # or sense another sidelink transmission in that slot.
                if node.scheduler.current_time in node.sending_times_resources:
                    # Half-duplex is a reception failure, not a missing PRR
                    # opportunity. Log it but do not feed the packet to the
                    # sensing/decoding path.
                    mac.log_forced_reception_failure(sender, packet, reason="half_duplex")
                    continue
            node.receive_packet(packet, resources)

    def link_budget(self, sender, receiver, tx_power_dbm):
        return self.propagation.link_budget(
            sender, receiver, receiver.scheduler.current_time, tx_power_dbm
        )

    def received_power_dbm(self, sender, receiver, tx_power_dbm):
        return self.link_budget(sender, receiver, tx_power_dbm).received_power_dbm

    def calculate_full_sinr(self, receiver, desired_sender, interfering_senders,
                            tx_power_dbm, allocated_subchannels,
                            bandwidth_mhz, subchannels_per_slot):
        """Per-subchannel SINR for the FULL profile.

        Total UE transmit power is divided uniformly across the subchannels
        occupied by its PSSCH allocation.  Interferers are already filtered by
        the MAC to those overlapping the resource currently being evaluated.
        """
        allocated_subchannels = max(1, int(allocated_subchannels))
        power_mode = os.environ.get("NRV2X_TX_POWER_MODE", "total").strip().lower()
        if power_mode not in ("total", "per_subchannel"):
            raise ValueError(f"Unsupported NRV2X_TX_POWER_MODE={power_mode!r}")
        # 3GPP/system-level default: P_t is the total UE transmit power over
        # the PSSCH allocation.  Therefore a two-subchannel allocation has
        # 3 dB less power per subchannel than a one-subchannel allocation.
        # per_subchannel is retained only as a diagnostic/legacy sensitivity.
        per_sc_tx_dbm = (
            float(tx_power_dbm) - 10.0 * math.log10(allocated_subchannels)
            if power_mode == "total" else float(tx_power_dbm)
        )
        desired = self.link_budget(desired_sender, receiver, per_sc_tx_dbm)
        desired_mw = _dbm_to_mw(desired.received_power_dbm)

        interference_mw = 0.0
        for interferer in interfering_senders:
            mac = getattr(interferer, "mac_layer", None)
            int_total_dbm = float(getattr(mac, "P_t_dB", tx_power_dbm))
            int_nsc = max(1, int(getattr(mac, "num_channels", allocated_subchannels)))
            int_per_sc_dbm = (
                int_total_dbm - 10.0 * math.log10(int_nsc)
                if power_mode == "total" else int_total_dbm
            )
            interference_mw += _dbm_to_mw(
                self.received_power_dbm(interferer, receiver, int_per_sc_dbm)
            )

        subchannel_bw_hz = float(bandwidth_mhz) * 1e6 / max(1, int(subchannels_per_slot))
        noise_dbm = -174.0 + 10.0 * math.log10(subchannel_bw_hz) + self.noise_figure_db
        noise_mw = _dbm_to_mw(noise_dbm)
        sinr_linear = desired_mw / max(noise_mw + interference_mw, 1e-300)
        rssi_mw = desired_mw + interference_mw + noise_mw

        return {
            "sinr_db": 10.0 * math.log10(max(sinr_linear, 1e-300)),
            "rssi_dbm": _mw_to_dbm(rssi_mw),
            "desired_dbm": desired.received_power_dbm,
            "noise_dbm": noise_dbm,
            "interference_dbm": _mw_to_dbm(interference_mw) if interference_mw > 0 else float("-inf"),
            "state": desired.state.value,
            "path_loss_db": desired.path_loss_db,
            "shadow_db": desired.shadow_fading_db,
            "blockage_db": desired.blockage_loss_db,
            "per_sc_tx_dbm": per_sc_tx_dbm,
            "tx_power_mode": power_mode,
        }
