import random
import logging
from collections import defaultdict
from resource_allocation import ResourceAllocation
from event import Event
import math
import csv
from collections import Counter
from collections import defaultdict
import os
from nrv2x.rc_policy import rc_bounds
from helpers import *
from sora import SORAEngine, SORAReport, ResourceState, HybridRSPacket, HybridRSTransmitter, HybridRSReceiver, future_resources_on_air
from sora.overhead import sparse_rs_bytes, compare_full_state_encodings, compare_delta_encodings
from sora.rs_codec import encode_rs, decode_rs

from nrv2x import (
    Mode2ResourceSelector, SensingRecord, CandidateResource, profile as nr_profile_config,
    SidelinkReceptionConfig, SidelinkReceptionModel, default_pssch_midpoint_db,
    system_level_sinr_threshold_db, uniform_priority_thresholds,
)


logger = logging.getLogger(__name__)

class MACLayer:
    def __init__(self, node, bandwidth_mhz, numerology, rri, L, H,  pk, num_channels, sensing_window_duration, available_percentage, periodic, protocol, initial_P_s_dB,P_t_dB, K_0_dB, alpha,SINR_threshold_dB, filename,  mu=0.2):
        self.node = node
        self.bandwidth_mhz = bandwidth_mhz
        self.numerology = numerology
        self.resource_allocation = ResourceAllocation(bandwidth_mhz, numerology, rri, L, H , node.scheduler, protocol, node)
        self.rri = rri
        self.pk = pk
        self.periodic = periodic 
        self.protocol = protocol
        self.initial_P_s_dB = initial_P_s_dB
        self.P_t_dB = P_t_dB
        self.K_0_dB = K_0_dB
        self.alpha = alpha
        self.available_percentage = available_percentage
        self.num_channels = num_channels
        self.N_0_dB = -174 + 10 * math.log10(num_channels*bandwidth_mhz* 1e6/self.resource_allocation.channels_per_slot)
        # print("self.N_0_dB", self.N_0_dB)
#initial_P_s_dB - SINR_threshold_dB
        self.sensing_window_duration = sensing_window_duration
        self.RC = 0
        self.rev_RC = 0
        self.intial_RC = 0
        self.sensed_resources = set()
        self.neighbor_resources = {}
        self.receive_buffer = []
        self.num_receive_buffer = []
        self.num_receive_buffer_CRA6G = []
        self.receive_timer = 0.005 / 1000
        self.num_receive_timer = 0.005 / 1000
        self.per_time = -1
        self.num_per_time = -1
        self.mu = mu

        # NR-V2X reconstruction profile.  LITE preserves the supplied protocol-0
        # path exactly; CORE/CORE_NO_REEVAL use the clean Release-16 selector.
        self.run_seed = int(os.environ.get("NRV2X_RUN_SEED", "1"))
        self._mac_rng = random.Random(f"{self.run_seed}|{self.node.node_id}|mac")
        self._selector_seed = (self.run_seed * 1000003 + (self.node.node_id if isinstance(self.node.node_id, int) else sum(map(ord, str(self.node.node_id))))) & 0x7fffffff
        self.nr_profile_name = os.environ.get("NRV2X_PROFILE", "lite").strip().lower()
        self.sl_priority = int(os.environ.get("NRV2X_SL_PRIORITY", "1"))
        if not 1 <= self.sl_priority <= 8:
            raise ValueError("NRV2X_SL_PRIORITY must be in 1..8")
        self.nr_selector = None
        self.nr_selected_candidate = None
        self.nr_needs_reevaluation = False
        if self.protocol == 0 and self.nr_profile_name != "lite":
            self.nr_selector = Mode2ResourceSelector(
                nr_profile_config(
                    self.nr_profile_name,
                    numerology=self.numerology,
                    bandwidth_mhz=self.bandwidth_mhz,
                    subchannels_per_slot=self.resource_allocation.channels_per_slot,
                    required_subchannels=self.num_channels,
                    rri_s=self.rri,
                    default_tx_priority=self.sl_priority,
                    rsrp_threshold_dbm=self.initial_P_s_dB,
                    priority_rsrp_thresholds_dbm=uniform_priority_thresholds(self.initial_P_s_dB),
                    minimum_candidate_percent=float(self.available_percentage),
                    enforce_minimum_candidate_percent=True,
                    packet_delay_budget_s=float(os.environ.get("NRV2X_PDB_S", "0.100")),
                    t2_s=float(os.environ.get("NRV2X_T2_S", "0.100")),
                    sensing_reference=os.environ.get("NRV2X_SENSING_RS", "pssch").strip().lower(),
                ),
                seed=self._selector_seed,
            )

        # SORA reconstruction profile. ``legacy`` preserves the supplied
        # protocol-3/CRA6G path. ``core`` and ``full`` place only the SORA
        # overlay on the same reconstructed Mode-2 foundation used by NR.
        self.sora_profile_name = os.environ.get("SORA_PROFILE", "legacy").strip().lower()
        if self.sora_profile_name not in ("legacy", "core", "full"):
            raise ValueError("SORA_PROFILE must be legacy, core, or full")
        self.sora_engine = None
        self.sora_future_candidate = None
        self.sora_feedback_events = 0
        self.sora_collision_triggers = 0
        self.sora_future_reselections = 0
        self.sora_fallback_selections = 0
        self.sora_nr_relaxation_events = 0
        self.sora_nr_relaxations_total = 0
        self.sora_map_relaxation_events = 0
        self.sora_map_relaxations_total = 0
        self.sora_rs_snapshots = 0
        self.sora_rs_entries_total = 0
        self.sora_rs_entries_max = 0
        self.sora_rs_mode = os.environ.get("SORA_RS_MODE", "snapshot").strip().lower()
        if self.sora_rs_mode not in ("snapshot", "hybrid"):
            raise ValueError("SORA_RS_MODE must be snapshot or hybrid")
        self.sora_rs_tx = HybridRSTransmitter(
            full_interval_s=float(os.environ.get("SORA_RS_FULL_INTERVAL_S", "1.0"))
        )
        self.sora_rs_rx = HybridRSReceiver()
        self.sora_rs_full_tx = 0
        self.sora_rs_delta_tx = 0
        self.sora_rs_invalid_delta_rx = 0
        self.sora_rs_truncation_events = 0
        self.sora_rs_early_full_due_cap = 0
        self.sora_rs_logical_bytes_total = 0
        self.sora_rs_wire_bytes_total = 0
        self.sora_rs_wire_tx = 0
        # Per-packet lossless encoding comparison. These counters do not alter
        # protocol semantics; they measure how many bytes the *same logical RS*
        # would require under each supported on-air representation.
        self.sora_rs_encoding_bytes_total = defaultdict(int)
        self.sora_rs_encoding_packets = defaultdict(int)
        self.sora_rs_adaptive_method_counts = defaultdict(int)
        self.sora_rs_preferred_encoding = os.environ.get(
            "SORA_RS_LOGICAL_ENCODING", "rice_gap"
        ).strip().lower()
        self.sora_rs_preferred_bytes_total = 0
        self.sora_rs_serialize = os.environ.get("SORA_RS_SERIALIZE", "0") != "0"
        self.sora_rs_codec_decode_ok = 0
        self.sora_rs_codec_decode_errors = 0
        self.sora_rs_codec_bytes_total = 0
        self.sora_advertise_future_resource = os.environ.get(
            "SORA_ADVERTISE_FUTURE_RESOURCE", "1"
        ) != "0"
        self.last_sora_feedback = None
        self.sora_collision_pending = False
        self.sora_enable_collision_feedback = os.environ.get("SORA_ENABLE_COLLISION_FEEDBACK", "1") != "0"
        self.sora_enable_future_resource = os.environ.get("SORA_ENABLE_FUTURE_RESOURCE", "1") != "0"
        self.sora_enable_future_verification = (
            self.sora_enable_future_resource
            and os.environ.get("SORA_ENABLE_FUTURE_VERIFICATION", "1") != "0"
        )
        self.sora_enable_future_promotion = (
            self.sora_enable_future_resource
            and os.environ.get("SORA_ENABLE_FUTURE_PROMOTION", "1") != "0"
        )
        if self.protocol == 3 and self.sora_profile_name != "legacy":
            # Re-use nr_selector as the common local Mode-2 sensing/candidate
            # engine. The SORAEngine filters that candidate set cooperatively.
            self.nr_profile_name = self.sora_profile_name
            sora_selector_profile = os.environ.get("SORA_SELECTOR_PROFILE", self.sora_profile_name).strip().lower()
            self.nr_selector = Mode2ResourceSelector(
                nr_profile_config(
                    sora_selector_profile,
                    numerology=self.numerology,
                    bandwidth_mhz=self.bandwidth_mhz,
                    subchannels_per_slot=self.resource_allocation.channels_per_slot,
                    required_subchannels=self.num_channels,
                    rri_s=self.rri,
                    default_tx_priority=self.sl_priority,
                    rsrp_threshold_dbm=self.initial_P_s_dB,
                    priority_rsrp_thresholds_dbm=uniform_priority_thresholds(self.initial_P_s_dB),
                    minimum_candidate_percent=float(self.available_percentage),
                    enforce_minimum_candidate_percent=(
                        os.environ.get("SORA_CANDIDATE_RULE", "map5").strip().lower() == "standard20"
                    ),
                    packet_delay_budget_s=float(os.environ.get("NRV2X_PDB_S", "0.100")),
                    t2_s=float(os.environ.get("NRV2X_T2_S", "0.100")),
                    sensing_reference=os.environ.get("NRV2X_SENSING_RS", "pssch").strip().lower(),
                ),
                seed=self._selector_seed,
            )
            self.sora_engine = SORAEngine(
                self.nr_selector,
                rri_s=self.rri,
                slots_per_rri=self.resource_allocation.total_slots,
                subchannels_per_slot=self.resource_allocation.channels_per_slot,
                seed=self._selector_seed + 7919,
                collision_threshold=float(os.environ.get("SORA_COLLISION_THRESHOLD", "0.8")),
                map_expiry_rri=float(os.environ.get("SORA_RS_EXPIRY_RRI", "2.0")),
                minimum_feedback_reports=int(os.environ.get("SORA_MIN_FEEDBACK_REPORTS", "1")),
                collision_mode=os.environ.get("SORA_COLLISION_MODE", "legacy_single").strip().lower(),
                strong_threshold=float(os.environ.get("SORA_STRONG_THRESHOLD", "0.9")),
                strong_neighbor_fraction=float(os.environ.get("SORA_STRONG_NEIGHBOR_FRACTION", "0.5")),
                enable_two_hop=os.environ.get("SORA_ENABLE_TWO_HOP", "1") != "0",
                reserve_announced_future=os.environ.get("SORA_RESERVE_ANNOUNCED_FUTURE", "0") != "0",
                enable_map_candidate_floor=(
                    os.environ.get("SORA_CANDIDATE_RULE", "map5").strip().lower() == "map5"
                ),
                map_candidate_floor_percent=float(os.environ.get("SORA_MAP_CANDIDATE_FLOOR_PERCENT", "5.0")),
                map_relax_step_db=float(os.environ.get("SORA_MAP_RELAX_STEP_DB", "3.0")),
                map_max_relaxations=int(os.environ.get("SORA_MAP_MAX_RELAXATIONS", "20")),
                map_initial_threshold_dbm=float(self.initial_P_s_dB),
                map_distance_from_threshold=lambda threshold_dbm: self.node.calculate_distance(
                    threshold_dbm, self.P_t_dB, self.K_0_dB, self.alpha
                ),
            )

        # FULL separates PSCCH/SCI acquisition from PSSCH payload decoding.
        # Publication default is a deterministic system-level threshold whose
        # MCS 4/11 landmarks come from the cited open-source NR-V2X model.
        # A logistic mode remains available for calibrated BLER sensitivity.
        self.nr_reception = None
        if (self.protocol == 0 and self.nr_profile_name == "full") or (self.protocol == 3 and self.sora_profile_name == "full"):
            raw_mcs = os.environ.get("NRV2X_MCS_INDEX")
            if raw_mcs is None:
                mcs_index = 4 if abs(float(SINR_threshold_dB) - 2.3) < abs(float(SINR_threshold_dB) - 10.13) else 11
            else:
                mcs_index = int(raw_mcs)
            packet_bytes = int(os.environ.get("NRV2X_PACKET_BYTES", "350"))
            pssch_threshold = float(os.environ.get(
                "NRV2X_PSSCH_THRESHOLD_DB",
                system_level_sinr_threshold_db(mcs_index, packet_bytes, SINR_threshold_dB),
            ))
            pssch_mid = float(os.environ.get(
                "NRV2X_PSSCH_BLER_MIDPOINT_DB",
                default_pssch_midpoint_db(mcs_index, pssch_threshold),
            ))
            reception_mode = os.environ.get("NRV2X_RECEPTION_MODEL", "threshold").strip().lower()
            sensitivity_mode = os.environ.get("NRV2X_RECEIVER_SENSITIVITY_MODE", "implicit").strip().lower()
            if sensitivity_mode not in ("implicit", "derived", "fixed"):
                raise ValueError(f"Unsupported NRV2X_RECEIVER_SENSITIVITY_MODE={sensitivity_mode!r}")
            noise_figure_db = float(os.environ.get("NRV2X_NOISE_FIGURE_DB", "9.0"))
            subchannel_bw_hz = self.bandwidth_mhz * 1e6 / max(1, self.resource_allocation.channels_per_slot)
            noise_per_sc_dbm = -174.0 + 10.0 * math.log10(subchannel_bw_hz) + noise_figure_db
            derived_sensitivity_dbm = noise_per_sc_dbm + pssch_threshold
            if sensitivity_mode == "implicit":
                receiver_power_gate_dbm = None
            elif sensitivity_mode == "derived":
                receiver_power_gate_dbm = derived_sensitivity_dbm
            else:
                receiver_power_gate_dbm = float(os.environ.get("NRV2X_RECEIVER_POWER_GATE_DBM", "-110.0"))
            self.nr_noise_per_sc_dbm = noise_per_sc_dbm
            self.nr_derived_sensitivity_dbm = derived_sensitivity_dbm
            self.nr_receiver_sensitivity_mode = sensitivity_mode
            self.nr_reception = SidelinkReceptionModel(SidelinkReceptionConfig(
                pscch_mode=os.environ.get("NRV2X_PSCCH_RECEPTION_MODEL", "threshold").strip().lower(),
                pscch_threshold_db=float(os.environ.get("NRV2X_PSCCH_THRESHOLD_DB", "-3.0")),
                pscch_midpoint_db=float(os.environ.get("NRV2X_PSCCH_BLER_MIDPOINT_DB", "-3.0")),
                pscch_transition_db=float(os.environ.get("NRV2X_PSCCH_BLER_TRANSITION_DB", "0.8")),
                pssch_mode=reception_mode,
                pssch_threshold_db=pssch_threshold,
                pssch_midpoint_db=pssch_mid,
                pssch_transition_db=float(os.environ.get("NRV2X_PSSCH_BLER_TRANSITION_DB", "1.0")),
                receiver_power_gate_dbm=receiver_power_gate_dbm,
                seed=int(os.environ.get("NRV2X_RECEPTION_SEED", "1")),
            ))

        self.current_resources = self.select_resources(num_channels)
        self.SINR_threshold_dB = SINR_threshold_dB  # Threshold for SINR (linear scale)
        self.filename = filename
        self.road_length=4000
        self.central_length=500
        self.central_start = (self.road_length - self.central_length) / 2
        self.central_end = self.central_start + self.central_length
        self.collided_resources = set()
        self.SINR_Margin_dB = SINR_threshold_dB /10
        self.RSSI_Margin_dB = initial_P_s_dB
        self.new_P_s_dB=initial_P_s_dB
        self.used_sinr = 0
        self.count_receive = 0
        self.future_resource = (
            self._select_sora_future_resource()
            if self.sora_core_enabled() and self.sora_enable_future_resource
            else ([] if self.sora_core_enabled() else self.select_resources(num_channels))
        )
        # Reconstructed NR/SORA start with a valid SPS counter.  The legacy
        # code intentionally retains its original first-event RC==0 behavior
        # for exact bridge reproduction.
        if self.common_core_enabled():
            self.RC = self.initialize_RC()
            self.intial_RC = self.RC
        self.ROI_Z=.2
        self.start_select= False
        self.RC_factor=10
        # if self.protocol==2:
        #     self.available_percentage=.05
   
               

    


    def calculate_sinr(self, receiving_node, transmitting_node, all_nodes):
        """Calculate per-resource SINR and desired received power.

        LITE/CORE deliberately preserve the original power-law implementation
        so the bridge experiments remain reproducible.  FULL switches only the
        common radio layer to the 3GPP V2V reconstruction.
        """
        if self.common_full_enabled() and hasattr(self, "channel_model"):
            result = self.channel_model.calculate_full_sinr(
                receiving_node, transmitting_node, all_nodes,
                tx_power_dbm=self.P_t_dB,
                allocated_subchannels=self.num_channels,
                bandwidth_mhz=self.bandwidth_mhz,
                subchannels_per_slot=self.resource_allocation.channels_per_slot,
            )
            # The second quantity is desired received power rather than total
            # RSSI.  This prevents a strong interferer from making a weak
            # desired packet appear to pass the receiver-power gate.
            return result["sinr_db"], result["desired_dbm"]

        P_t = 10 ** (self.P_t_dB / 10)
        K_0 = 10 ** (self.K_0_dB / 10)
        N_0 = 10 ** (self.N_0_dB / 10)

        d_ij = self.calculate_distance(receiving_node.position, transmitting_node.position)
        desired_signal = calculate_desired_signal(P_t, K_0, d_ij, self.alpha)
        interference = calculate_interference(P_t, K_0, receiving_node, all_nodes, transmitting_node, self.alpha)

        sinr = desired_signal / (interference + N_0)
        RSSI = desired_signal + interference
        return 10 * math.log10(sinr), 10 * math.log10(RSSI)




    def calculate_distance(self, position1, position2):
        return math.sqrt((position1[0] - position2[0]) ** 2 + (position1[1] - position2[1]) ** 2)
    
    def initialize_RC(self):
        """Initialize SPS RC with protocol-specific configurable policies.

        NR-V2X defaults to the standard RC range. SORA keeps its historical
        factor-10 policy by default, but both remain explicit CLI options.
        """
        if self.protocol == 3:
            mode = os.environ.get("SORA_RC_MODE", "factor10")
            factor = float(os.environ.get("SORA_RC_FACTOR", "10"))
        else:
            mode = os.environ.get("NRV2X_RC_MODE", "standard")
            factor = float(os.environ.get("NRV2X_RC_FACTOR", "10"))
        lo, hi = rc_bounds(self.rri, mode=mode, factor=factor)
        return self._mac_rng.randint(lo, hi)


    def create_sci2_header(self):
        """Create common sidelink control information.

        SORA's prepared future resource is ADVERTISED by default to direct
        one-hop neighbors.  Direct receivers may avoid that future resource
        when selecting/verifying their own prepared future.  The advertisement
        is kept outside the ordinary RS map and is never relayed to two hops.
        Set ``SORA_ADVERTISE_FUTURE_RESOURCE=0`` (or use the CLI disable flag)
        to reproduce the private-future baseline.
        """
        if self.protocol == 3:
            future_on_air = list(future_resources_on_air(
                self.future_resource,
                enabled=getattr(self, "sora_enable_future_resource", True),
                advertise=getattr(self, "sora_advertise_future_resource", True),
            ))
        else:
            # Preserve legacy protocol-1/2 behavior outside the SORA core.
            future_on_air = self.future_resource
        return {
            'resources': self.current_resources,
            'reservation_time': self.rri * self.RC,
            'future_resource': future_on_air,
            'switching_probability': 1-self.pk,
            'position': tuple(self.node.position),  # simulator control metadata
            'node_id': self.node.node_id,
            'priority': getattr(self, 'sl_priority', 1)
        }

    def create_packet(self, data):
        collided_resources = set(self.collided_resources)  # Copy the set here
        sci2_header = self.create_sci2_header()
        rs_meta = None
        rs_wire = None
        rs_wire_method = None
        if self.sora_core_enabled():
            max_entries = int(os.environ.get("SORA_RS_MAX_ENTRIES", "0"))
            now_s = self.node.scheduler.current_time

            # Build the complete sparse local RS first so we can report whether
            # a practical fixed-budget variant had to truncate lower-priority
            # cooperative information.  The SORAEngine's deterministic cap
            # retains direct ONE_HOP information before more distant TWO_HOP
            # information.
            uncapped_state = self.sora_engine.snapshot(
                now_s, self.current_resources, sparse=True
            )
            if max_entries > 0 and len(uncapped_state) > max_entries:
                self.sora_rs_truncation_events += 1
                bounded_state = self.sora_engine.snapshot(
                    now_s, self.current_resources, sparse=True,
                    max_entries=max_entries,
                )
            else:
                bounded_state = uncapped_state

            if self.sora_rs_mode == "hybrid":
                # Hybrid semantics: periodic FULL baseline plus DELTA updates
                # relative to that baseline.  If a bounded delta would exceed
                # the entry cap, HybridRSTransmitter emits an early bounded FULL
                # refresh rather than silently dropping changed states.
                periodic_due = (
                    self.sora_rs_tx._seq < 0
                    or not self.sora_rs_tx._baseline
                    or float(now_s) - self.sora_rs_tx._last_full_s
                    >= float(self.sora_rs_tx.full_interval_s) - 1e-12
                )
                encoded = self.sora_rs_tx.encode(
                    now_s, bounded_state,
                    max_entries=(max_entries if max_entries > 0 else None),
                )
                rs_payload = dict(encoded.states)
                rs_meta = {"mode": encoded.mode, "epoch": encoded.epoch, "seq": encoded.seq}
                if encoded.mode == "full":
                    self.sora_rs_full_tx += 1
                    if not periodic_due:
                        self.sora_rs_early_full_due_cap += 1
                else:
                    self.sora_rs_delta_tx += 1
            else:
                rs_payload = dict(bounded_state)

            n_entries = len(rs_payload)
            self.sora_rs_snapshots += 1
            self.sora_rs_entries_total += n_entries
            self.sora_rs_entries_max = max(self.sora_rs_entries_max, n_entries)

            # Logical encoding measurement (what the sparse report actually
            # needs) is kept separately from physical accounting.  In
            # fixed-cap mode, the PHY uses a constant padded RS budget per SPS
            # packet; in ``none`` mode this counter is analysis-only.
            logical = sparse_rs_bytes(
                entry_count=n_entries,
                rri_s=self.rri,
                numerology=int(os.environ.get("NRV2X_NUMEROLOGY", "0")),
                bandwidth_mhz=int(round(self.bandwidth_mhz)),
                state_bits=int(os.environ.get("SORA_RS_STATE_BITS", "2")),
                ce_header_bytes=int(os.environ.get("SORA_RS_CE_HEADER_BYTES", "2")),
                hybrid_meta_bytes=(
                    int(os.environ.get("SORA_RS_HYBRID_META_BYTES", "3"))
                    if self.sora_rs_mode == "hybrid" else 0
                ),
            )
            # Preserve the historical sparse-index counter for backwards
            # compatibility, then additionally measure every implemented
            # lossless encoding on the exact logical packet.
            self.sora_rs_logical_bytes_total += int(logical["total_bytes"])
            packet_mode = (rs_meta.get("mode") if rs_meta else "snapshot")
            enc_kwargs = dict(
                rri_s=self.rri,
                numerology=int(os.environ.get("NRV2X_NUMEROLOGY", "0")),
                bandwidth_mhz=int(round(self.bandwidth_mhz)),
                ce_header_bytes=int(os.environ.get("SORA_RS_CE_HEADER_BYTES", "2")),
                hybrid_meta_bytes=(
                    int(os.environ.get("SORA_RS_HYBRID_META_BYTES", "3"))
                    if self.sora_rs_mode == "hybrid" else 0
                ),
            )
            # Main implementation: Golomb-Rice gap coding only.  The codec
            # chooses the Rice parameter k per packet to minimize the exact
            # encoded gap length.  We keep one metric stream for the actual
            # on-air representation rather than comparing unrelated codecs.
            if packet_mode == "delta":
                encoding_table = compare_delta_encodings(rs_payload, **enc_kwargs)
            else:
                encoding_table = compare_full_state_encodings(rs_payload, **enc_kwargs)
            preferred = "rice_gap"
            gr_result = encoding_table[preferred]
            self.sora_rs_encoding_bytes_total[preferred] += int(gr_result["total_bytes"])
            self.sora_rs_encoding_packets[preferred] += 1
            self.sora_rs_preferred_bytes_total += int(gr_result["total_bytes"])

            # Optional real serialization path.  When enabled, the packet carries
            # the compressed RS byte string and the receiver reconstructs the
            # logical map from those bytes.  This is deliberately separate from
            # the PHY byte-budget switch so codec correctness can be tested with
            # exactly the same packet size/seed and therefore exactly the same
            # radio conditions.
            if self.sora_rs_serialize:
                codec = encode_rs(
                    rs_payload,
                    mode=packet_mode,
                    method=preferred,
                    rri_s=self.rri,
                    numerology=int(os.environ.get("NRV2X_NUMEROLOGY", "0")),
                    bandwidth_mhz=int(round(self.bandwidth_mhz)),
                )
                rs_wire = codec.wire
                rs_wire_method = codec.selected_method
                self.sora_rs_codec_bytes_total += len(rs_wire)

            wire_bytes = int(os.environ.get("SORA_RS_WIRE_BYTES", "0"))
            if wire_bytes > 0:
                self.sora_rs_wire_bytes_total += wire_bytes
                self.sora_rs_wire_tx += 1
        else:
            rs_payload = self.resource_allocation.RS_dict

        full_packet = {
            'data': data,
            'sci2': sci2_header,
            'sender': self.node.node_id,
            'send_time': self.node.scheduler.current_time,
            'RS': (None if (self.sora_core_enabled() and self.sora_rs_serialize) else rs_payload),
            'RS_wire': rs_wire,
            'RS_wire_method': rs_wire_method,
            'RS_meta': rs_meta,
            'collided_resources': collided_resources
        }
        return full_packet

    def check_and_adjust_neighbors(self):
        self.new_P_s_dB=self.initial_P_s_dB
        current_P_s_dB = self.initial_P_s_dB
        total_resources = self.resource_allocation.total_slots * self.resource_allocation.channels_per_slot
        
        available_resources = self.calculate_total_free_channels(self.resource_allocation.get_free_resources())
        
        X = (available_resources / total_resources) * 100
        if self.node.node_id == 152:
           # print("received_RS",received_RS)
            print("nodeid: ", self.node.node_id , self.node.scheduler.current_time , X)
        while X < self.available_percentage:
            original_neighbors = self.node.neighbors.copy()
            current_P_s_dB += 3
            new_distance = self.node.calculate_distance(current_P_s_dB, self.node.P_t_dB, self.node.K_0_dB, self.node.alpha)
            distant_neighbors=self.node.find_neighbors_above_distance(new_distance)
            #distant_neighbors=self.node.find_neighbors_above_distance(new_distance)
            # Release resources used by distant neighbors
            self.release_distant_neighbors_resources(self.node, new_distance)
            available_resources = self.calculate_total_free_channels(self.resource_allocation.get_free_resources())
            X = (available_resources / total_resources) * 100
            self.new_P_s_dB=current_P_s_dB  
        return True


    def release_distant_neighbors_resources(self, node, new_distance):
        """
        Release the resources used by neighbors that are beyond the new sensing distance.
    
        Parameters:
        - node: The current node object.
        - new_distance: Updated sensing distance based on the new sensing power.
        """
        distant_neighbors = node.find_neighbors_above_distance(new_distance)
        for neighbor_id, neighbor_data in distant_neighbors:
            neighbor = node.scheduler.node_manager.nodes[neighbor_id]
            node.mac_layer.resource_allocation.release_resources(neighbor.mac_layer.current_resources)    

    def check_and_adjust_neighbors_CRA6G(self):
        """
        Adjusts neighbors and resources based on the availability percentage.
        """
        self.new_P_s_dB = self.initial_P_s_dB
        current_P_s_dB = self.initial_P_s_dB
        total_resources = self.resource_allocation.total_slots * self.resource_allocation.channels_per_slot
    
        # Calculate available resources
        available_resources = self.calculate_total_free_channels(
            self.resource_allocation.get_free_resources_from_RS_grid_1()
        )
        X = (available_resources / total_resources) * 100
        if self.node.node_id == 333:
           # print("received_RS",received_RS)
            print("nodeid: ", self.node.node_id , self.node.scheduler.current_time , X)
        while X < self.available_percentage:
            # Increase power and recalculate distance threshold
            current_P_s_dB += 3
            self.new_P_s_dB = current_P_s_dB
            new_distance = self.node.calculate_distance(
                current_P_s_dB, self.node.P_t_dB, self.node.K_0_dB, self.node.alpha
            )
    
            # Process RS_grid and Distance_grid
            updated = False
            for slot_idx, slot in enumerate(self.resource_allocation.RS_grid):
                for channel_idx, resource_value in enumerate(slot):
                    resource_distance = self.resource_allocation.Distance_grid[slot_idx][channel_idx]
    
                    # If resource value is 2 and distance exceeds threshold
                    if resource_value == 2 and resource_distance > new_distance:
                        self.resource_allocation.Distance_grid[slot_idx][channel_idx] = 0
                        self.resource_allocation.RS_grid[slot_idx][channel_idx] = self.resource_allocation.H
                        updated = True
    
            # If no updates for value 2, check for value 1
            if not updated:
                for slot_idx, slot in enumerate(self.resource_allocation.RS_grid):
                    for channel_idx, resource_value in enumerate(slot):
                        resource_distance = self.resource_allocation.Distance_grid[slot_idx][channel_idx]
    
                        # If resource value is 1 and distance exceeds threshold
                        if resource_value == 1 and resource_distance > new_distance:
                            self.resource_allocation.Distance_grid[slot_idx][channel_idx] = 0
                            self.resource_allocation.RS_grid[slot_idx][channel_idx] = self.resource_allocation.H
    
            # Recalculate available resources and X
            available_resources = self.calculate_total_free_channels(
                self.resource_allocation.get_free_resources_from_RS_grid_1()
            )
            X = (available_resources / total_resources) * 100
                    # Debugging output for specific node
            if self.node.node_id == 333:
           # print("received_RS",received_RS)
                print("nodeid: ", self.node.node_id , self.node.scheduler.current_time, "X",  X )
                print("after RS_grid:", self.node.node_id ,self.resource_allocation.RS_grid)
                print("after Distance_grid:", self.resource_allocation.Distance_grid)
        
        return True


    
 


    
        

    def nr_core_enabled(self):
        return self.protocol == 0 and self.nr_selector is not None

    def nr_full_enabled(self):
        return self.protocol == 0 and self.nr_profile_name == "full" and self.nr_selector is not None

    def sora_core_enabled(self):
        return self.protocol == 3 and self.sora_engine is not None

    def sora_full_enabled(self):
        return self.sora_core_enabled() and self.sora_profile_name == "full"

    def common_core_enabled(self):
        return self.nr_core_enabled() or self.sora_core_enabled()

    def common_full_enabled(self):
        return self.nr_full_enabled() or self.sora_full_enabled()

    def _nr_candidate_to_resources(self, candidate):
        # The legacy scheduler stores a resource as a phase within one RRI.
        # CORE stores an absolute slot.  Mapping modulo slots/RRI preserves the
        # selected transmission phase while the existing scheduler repeats it.
        phase_slot = candidate.abs_slot % self.resource_allocation.total_slots
        return [(phase_slot, ch) for ch in candidate.subchannels]

    def _nr_desired_rsrp_dbm(self, receiving_node, transmitting_node):
        """Approximate PSCCH/PSSCH RSRP on the DMRS/reference-RE scale.

        The old simulator compared a sensing threshold with total/subchannel
        received power. Release-16 sensing thresholds are RSRP thresholds. For
        the system-level abstraction we assume equal power per active
        subcarrier/RE and a 10-PRB subchannel unless explicitly overridden.
        """
        subch_prb = int(os.environ.get("NRV2X_SUBCHANNEL_SIZE_PRB", "10"))
        active_subcarriers = max(1, self.num_channels * subch_prb * 12)
        per_re_tx_dbm = self.P_t_dB - 10.0 * math.log10(active_subcarriers)
        if self.common_full_enabled() and hasattr(self, "channel_model"):
            return self.channel_model.received_power_dbm(
                transmitting_node, receiving_node, per_re_tx_dbm
            )
        P_t = 10 ** (per_re_tx_dbm / 10)
        K_0 = 10 ** (self.K_0_dB / 10)
        d_ij = self.calculate_distance(receiving_node.position, transmitting_node.position)
        desired = calculate_desired_signal(P_t, K_0, d_ij, self.alpha)
        return 10 * math.log10(max(desired, 1e-300))

    def _nr_observe_sci(self, receive_time, packet, resources, sinr_db, sender_node):
        if not self.common_core_enabled():
            return False
        # CORE keeps the deterministic bridge gate. FULL decodes PSCCH/SCI
        # independently with its own BLER curve, even when the PSSCH payload
        # later fails.
        if self.common_full_enabled() and self.nr_reception is not None:
            if os.environ.get("NRV2X_RELAXED_SCI", "0") == "1":
                # Relaxed Full sensitivity profile: SCI is considered available
                # when its reference-signal RSRP satisfies the Mode-2 sensing
                # threshold. PSSCH decoding remains unchanged and MCS/SINR based.
                rsrp_probe = self._nr_desired_rsrp_dbm(self.node, sender_node)
                threshold = self.nr_selector.cfg.threshold_for(
                    int(packet.get('sci2', {}).get('priority', 1)),
                    int(getattr(self.nr_selector.cfg, 'default_tx_priority', 1)),
                )
                if rsrp_probe < threshold:
                    return False
            else:
                sci_ok, _, _ = self.nr_reception.decode_pscch(
                    sinr_db,
                    sender_id=packet['sender'], receiver_id=self.node.node_id,
                    packet_id=packet.get('data'), timestamp=receive_time,
                )
                if not sci_ok:
                    return False
        else:
            sci_sinr_threshold_db = float(os.environ.get("NRV2X_PSCCH_SINR_DB", "-3.0"))
            if sinr_db < sci_sinr_threshold_db:
                return False
        abs_slot = self.nr_selector.time_to_slot_floor(receive_time)
        subchannels = tuple(sorted(ch for _, ch in resources))
        rsrp_dbm = self._nr_desired_rsrp_dbm(self.node, sender_node)
        return self.nr_selector.observe_sci(SensingRecord(
            timestamp_s=receive_time,
            abs_slot=abs_slot,
            subchannels=subchannels,
            rsrp_dbm=rsrp_dbm,
            tx_id=packet['sender'],
            tx_priority=int(packet.get('sci2', {}).get('priority', 1)),
            reservation_period_s=self.rri,
            sci_decoded=True,
        ))

    def bind_scheduled_candidate(self, transmission_time_s):
        """Bind SORA's phase resource to the actual upcoming absolute slot."""
        if not self.sora_core_enabled() or not self.current_resources:
            return
        abs_slot = self.nr_selector.time_to_slot_floor(transmission_time_s)
        subchannels = tuple(sorted(ch for _, ch in self.current_resources))
        self.nr_selected_candidate = CandidateResource(abs_slot, subchannels)
        # Do not arm re-evaluation on every periodic SPS transmission.  The
        # flag is armed only when a resource is newly selected/reselected.
        # This matches the intended pre-use re-evaluation role and avoids
        # repeatedly reselecting an already-established SPS reservation.

    def _record_sora_candidate_result(self, result):
        """Track local-NR and SORA-map candidate-floor relaxation separately."""
        if not self.sora_core_enabled() or result is None:
            return
        nr_relaxations = int(getattr(getattr(result, "nr_candidates", None), "relaxations", 0) or 0)
        self.sora_nr_relaxations_total += nr_relaxations
        if nr_relaxations > 0:
            self.sora_nr_relaxation_events += 1
        map_relaxations = int(getattr(result, "map_relaxations", 0) or 0)
        self.sora_map_relaxations_total += map_relaxations
        if map_relaxations > 0:
            self.sora_map_relaxation_events += 1

    def nr_core_reevaluate_scheduled(self):
        """Common pre-use re-evaluation; SORA additionally checks cooperative RS."""
        if not self.common_core_enabled() or not self.nr_needs_reevaluation:
            return False
        if self.nr_selected_candidate is None:
            self.nr_needs_reevaluation = False
            return False

        if self.sora_core_enabled():
            local = self.nr_selector.reevaluate(
                self.nr_selected_candidate, self.node.scheduler.current_time,
                tx_priority=self.sl_priority
            )
            cooperative_ok = self.sora_engine.candidate_is_cooperatively_free(
                self.nr_selected_candidate, self.node.scheduler.current_time
            )
            if (not local.changed) and cooperative_ok:
                self.nr_needs_reevaluation = False
                return False
            result = self.sora_engine.select(
                self.node.scheduler.current_time, tx_priority=self.sl_priority,
                exclude_phase=self.future_resource,
            )
            self._record_sora_candidate_result(result)
            if result.used_fallback:
                self.sora_fallback_selections += 1
            self.nr_selected_candidate = result.selected
            self.current_resources = self._nr_candidate_to_resources(result.selected)
            self.nr_needs_reevaluation = False
            return True

        result = self.nr_selector.reevaluate(
            self.nr_selected_candidate, self.node.scheduler.current_time,
            tx_priority=self.sl_priority
        )
        self.nr_selected_candidate = result.selected
        self.current_resources = self._nr_candidate_to_resources(result.selected)
        self.nr_needs_reevaluation = False
        return result.changed

    def calculate_total_free_channels(self, free_channel):
        return sum(len(channels) for channels in free_channel.values())

    def select_resources(self, num_channels):
        if self.periodic:
            if self.protocol == 3:
                if self.sora_core_enabled():
                    result = self.sora_engine.select(
                        self.node.scheduler.current_time, tx_priority=self.sl_priority
                    )
                    self._record_sora_candidate_result(result)
                    if result.used_fallback:
                        self.sora_fallback_selections += 1
                    self.nr_selected_candidate = result.selected
                    self.nr_needs_reevaluation = self.nr_selector.cfg.use_reevaluation
                    resources = self._nr_candidate_to_resources(result.selected)
                else:
                    self.check_and_adjust_neighbors_CRA6G()
                    resources = self.resource_allocation.select_contiguous_resources_from_RS_grid(self.num_channels)
                    if not resources and hasattr(self, "current_resources"):
                        resources = self.current_resources
            else:
                if self.protocol == 0 and self.nr_core_enabled():
                    candidate = self.nr_selector.select(self.node.scheduler.current_time, tx_priority=self.sl_priority)
                    self.nr_selected_candidate = candidate
                    self.nr_needs_reevaluation = self.nr_selector.cfg.use_reevaluation
                    resources = self._nr_candidate_to_resources(candidate)
                else:
                    self.check_and_adjust_neighbors()
                    resources = self.resource_allocation.select_random_contiguous_channels(self.num_channels)
            
            return resources

        
    def _select_sora_future_resource(self):
        if not self.sora_core_enabled():
            return self.select_resources(self.num_channels)
        exclude = getattr(self, "current_resources", ())
        result = self.sora_engine.select_future(
            self.node.scheduler.current_time, tx_priority=self.sl_priority,
            exclude_phase=exclude, stream="sora",
        )
        self._record_sora_candidate_result(result)
        if result.used_fallback:
            self.sora_fallback_selections += 1
        self.sora_future_candidate = result.selected
        return self._nr_candidate_to_resources(result.selected)

    def resource_update(self, resources):
         for resource in resources:
            slot, channel = resource
            self.resource_allocation.update_resources(slot, [channel], True)

    def select_and_update_resources(self):
        """Select and update current and future resources."""
        self.current_resources = self.select_resources(self.num_channels)
        self.resource_allocation.update_RS_grid(self.node.node_id, self.current_resources,self.future_resource, self.node.scheduler.current_time,self.resource_allocation.RS_dict, 0)
        self.resource_update(self.current_resources)


    def select_and_update_future_resources(self, check_resources):
        """Select and update current and future resources."""
        per_future_resource=check_resources
        if self.sora_core_enabled():
            if not self.sora_enable_future_resource:
                self.future_resource = []
                return
            self.future_resource = self._select_sora_future_resource()
            self.sora_future_reselections += 1
        else:
            self.future_resource = self.select_resources(self.num_channels)
        #print(" self.node.node_id",  self.node.node_id, "check_resources", check_resources, "self.future_resource", self.future_resource)
        #self.resource_allocation.release_resources_future(per_future_resource,self.node.scheduler.current_time)
        self.resource_allocation.update_RS_grid(self.node.node_id, self.current_resources,self.future_resource, self.node.scheduler.current_time,self.resource_allocation.RS_dict, 0)
       #self.resource_update(self.future_resource)


   
    def process_received_messages_before_sending(self):
        """Process received control information and report a SORA collision trigger."""
        collision_triggered = False
        if self.protocol == 2:
            self.action_process_received_messages()
        if self.protocol == 3:
            if self.sora_core_enabled():
                if self.sora_enable_collision_feedback:
                    feedback = self.sora_engine.evaluate_collision_feedback()
                    self.last_sora_feedback = feedback
                    if feedback.acknowledgement_ratio is not None:
                        self.sora_feedback_events += 1
                    if feedback.collision_suspected and self.rev_RC > 0:
                        collision_triggered = True
                        self.sora_collision_pending = True
                        self.sora_collision_triggers += 1
                if self.sora_enable_future_verification:
                    self.reevaluate_future_resources()
            else:
                self.action_process_received_messages_CRA6G()
        return collision_triggered

    def initialize_resources_if_needed(self):
        """Initialize resources if the resource counter (RC) is -1."""
        if self.protocol == 2 and self.RC == -1:
            free_resources = self.resource_allocation.get_free_resources()
    
            if not check_future_resources(self.future_resource, free_resources):
                # Select new current and future resources
                self.select_and_update_resources()
            else:
                # Switch to future resource and select a new future resource
                self.current_resources = self.future_resource
                self.resource_update(self.current_resources)
                self.future_resource = self.select_resources(self.num_channels)
    
            # Initialize the resource counter (RC)
            self.RC = self.initialize_RC()
            self.rev_RC=0
            self.intial_RC=self.RC
    
            # Schedule the next transmission for protocol-2 initialization.
            if self.current_resources:
                self.node.schedule_next_transmission_based_on_new_resources()

    def reevaluate_future_resources(self):
        """Continuously verify SORA's prepared future resource."""
        if self.protocol != 3:
            return
        if self.sora_core_enabled():
            if not self.sora_enable_future_resource or not self.sora_enable_future_verification:
                return
            if not self.sora_engine.future_is_available(
                self.future_resource,
                self.node.scheduler.current_time,
                tx_priority=self.sl_priority,
                exclude_phase=getattr(self, "current_resources", ()),
            ):
                self.future_resource = self._select_sora_future_resource()
                self.sora_future_reselections += 1
            return

        free_resources = self.resource_allocation.get_free_resources_from_RS_grid_1()
        is_avilabe, check_resources = check_future_resources(self.future_resource, free_resources)
        if not is_avilabe:
            self.select_and_update_future_resources(check_resources)

    def reevaluate_resources(self, schedule_next=True):
        """Reevaluate resources when the resource counter (RC) reaches zero."""

            
            
        if self._mac_rng.random() > self.pk:


            if self.protocol == 3:
                if self.sora_core_enabled():
                    if self.sora_enable_future_verification:
                        self.reevaluate_future_resources()
                    if self.sora_enable_future_promotion:
                        self.current_resources = list(self.future_resource)
                    else:
                        # Ablation: choose current resource on demand rather than
                        # promoting the prepared future candidate.
                        result = self.sora_engine.select(
                            self.node.scheduler.current_time, tx_priority=self.sl_priority
                        )
                        if result.used_fallback:
                            self.sora_fallback_selections += 1
                        self.current_resources = self._nr_candidate_to_resources(result.selected)
                    self.future_resource = (
                        self._select_sora_future_resource() if self.sora_enable_future_resource else []
                    )
                    self.start_select = True
                    self.nr_needs_reevaluation = self.nr_selector.cfg.use_reevaluation
                elif not self.start_select:
                    self.resource_allocation.release_resources(self.current_resources)
                    self.select_and_update_resources()
                    check_resources=[]
                    self.select_and_update_future_resources(check_resources)
                    self.start_select=True
                else:
                    self.resource_allocation.release_resources(self.current_resources)
                    self.reevaluate_future_resources()
                    self.current_resources = self.future_resource
                    self.future_resource = self.select_resources(self.num_channels)
                    self.resource_allocation.update_RS_grid(self.node.node_id, self.current_resources,self.future_resource, self.node.scheduler.current_time,self.resource_allocation.RS_dict, 0)
                                
            if self.protocol == 0:       
                            # Release current resources and select new ones
                self.resource_allocation.release_resources(self.current_resources)
                self.select_and_update_resources()
                    

                #self.select_and_update_resources()
                
    
            if self.protocol == 2:
                free_resources = self.resource_allocation.get_free_resources()
                if not check_future_resources(self.future_resource, free_resources):
                    self.select_and_update_resources()
                else:
                    self.current_resources = self.future_resource
                    self.resource_update(self.current_resources)
                    self.future_resource = self.select_resources(self.num_channels)
    
            # Legacy callers can request immediate scheduling. Reconstructed
            # callers let Node preserve/reschedule the same application packet.
            if schedule_next and self.current_resources:
                self.node.schedule_next_transmission_based_on_new_resources()
        else:
            if self.protocol == 2:
                # Release the future resource and select a new one
                self.resource_allocation.release_resources(self.future_resource)
                self.future_resource = self.select_resources(self.num_channels)
    
        # Reset the resource counter (RC)
        self.RC = self.initialize_RC()
        self.rev_RC=0
        self.intial_RC=self.RC
    
    def transmit_packet(self, data):
        """Transmit one application packet and return whether radio TX occurred."""
        if self.current_resources:
            full_packet = self.create_packet(data=data)
            self.node.successful_transmissions += 1
            self.node.total_resources_used += len(self.current_resources)
            update_resource_tracker(self.node, self.current_resources)
            if self.common_core_enabled():
                self.nr_selector.mark_own_tx(
                    self.nr_selector.time_to_slot_floor(self.node.scheduler.current_time)
                )
            self.node.mac_layer.channel_model.transmit(self.node, full_packet, self.current_resources)
            if self.sora_core_enabled():
                self.sora_engine.start_feedback_window(self.current_resources)
            self.collided_resources.clear()
            return True
        logger.warning(
            f"Time {self.node.scheduler.current_time}ms: Node {self.node.node_id} "
            f"could not find {self.num_channels} contiguous channels"
        )
        return False

    def send_packet(self, packet):
        """Process one due packet and explicitly report TX/defer/drop status.

        Core/Full use corrected counter semantics: every normal SPS opportunity
        transmits a packet; the RC is decremented *after* transmission and a
        new resource is prepared for the next opportunity when it expires.
        A SORA collision trigger is different: the already generated packet is
        moved to the prepared future-resource phase and therefore returned as
        ``deferred`` rather than silently discarded.
        """
        collision_triggered = self.process_received_messages_before_sending()
        self.resource_allocation.release_unused_resources(self.node.scheduler.current_time, self.rri)
        self.initialize_resources_if_needed()

        # Preserve the original control flow for legacy bridge runs.
        if not self.common_core_enabled():
            if self.RC == 0:
                self.reevaluate_resources()
                return {"status": "deferred", "reason": "legacy_rc_reselection", "resources": list(self.current_resources)}
            self.RC -= 1
            self.rev_RC += 1
            self.reevaluate_future_resources()
            self.resource_allocation.update_RS_grid(
                self.node.node_id, self.current_resources, self.future_resource,
                self.node.scheduler.current_time, self.resource_allocation.RS_dict, 0
            )
            ok = self.transmit_packet(packet.get("data", "payload data"))
            return {"status": "transmitted" if ok else "dropped", "reason": "legacy", "resources": list(self.current_resources)}

        # SORA's collision feedback can require moving this already-generated
        # packet to the future-resource phase.  Do not count it as a dropped
        # application packet and do not generate a replacement packet.
        if self.sora_core_enabled() and collision_triggered:
            self.reevaluate_resources(schedule_next=False)
            self.sora_collision_pending = False
            return {
                "status": "deferred",
                "reason": "sora_collision_reselection",
                "resources": list(self.current_resources),
            }

        # Safety for any externally forced zero counter: prepare a valid
        # resource before the packet, but normal expiry is handled post-TX.
        if self.RC <= 0:
            self.reevaluate_resources(schedule_next=False)

        if self.sora_core_enabled():
            self.reevaluate_future_resources()
            self.resource_allocation.update_RS_grid(
                self.node.node_id, self.current_resources, self.future_resource,
                self.node.scheduler.current_time, self.resource_allocation.RS_dict, 0
            )

        tx_resources = list(self.current_resources)
        ok = self.transmit_packet(packet.get("data", "payload data"))
        if not ok:
            return {"status": "dropped", "reason": "no_resource", "resources": tx_resources}

        self.rev_RC += 1
        self.RC -= 1

        # Normal SPS expiry prepares the resource for the *next* packet.  It no
        # longer consumes/skips an application opportunity.
        if self.RC <= 0:
            self.reevaluate_resources(schedule_next=False)

        return {"status": "transmitted", "reason": "ok", "resources": tx_resources}

    def log_forced_reception_failure(self, sender_node, packet, reason="half_duplex"):
        """Record a receiver opportunity that cannot be decoded by construction.

        PRR denominators must include half-duplex losses.  The original channel
        path skipped a UE that was transmitting in the same slot, making that
        TX-RX opportunity disappear from the CSV entirely.  This logger records
        it as success=0 without passing the packet into sensing/decoding.
        """
        _dist = self.calculate_distance(self.node.position, sender_node.position)
        _log_max = float(os.environ.get('SORA_RX_LOG_MAX_DISTANCE_M', 'inf'))
        if _dist > _log_max:
            return
        row = {
            'timestamp': self.node.scheduler.current_time,
            'sender_id': sender_node.node_id,
            'receiver_id': self.node.node_id,
            'packet_id': packet.get('data'),
            'distance': _dist,
            'success': 0,
        }
        write_header = not os.path.exists(self.filename)
        with open(self.filename, 'a', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=['timestamp', 'sender_id', 'receiver_id', 'packet_id', 'distance', 'success'])
            if write_header:
                writer.writeheader()
            writer.writerow(row)

    def process_received_messages(self):
        """Process one receive batch with resource-indexed interference lookup.

        This is semantically equivalent to the original implementation, but it
        avoids repeatedly rescanning ``receive_buffer`` for every sender and
        resource.  At dense SUMO loads that original nested scan dominated
        runtime.  The optimized path builds the sender/resource indexes once
        per batch and then reuses them for SINR, sensing, payload decoding, and
        CSV accounting.
        """
        self.RSSI_Margin_dB = self.new_P_s_dB
        if not self.receive_buffer:
            return

        # Freeze the batch because successful message processing may schedule
        # future events.  Preserve the first packet for a sender, matching the
        # original first-match scans below.
        batch = list(self.receive_buffer)
        packet_by_sender = {}
        resources_by_sender = {}
        sender_order = []
        resource_to_senders = defaultdict(list)
        for receive_time, packet, resources in batch:
            sender_id = packet['sender']
            if sender_id not in packet_by_sender:
                packet_by_sender[sender_id] = (receive_time, packet, resources)
                resources_by_sender[sender_id] = resources
                sender_order.append(sender_id)
            for resource in resources:
                # The resource objects are tuples in the reconstructed engine;
                # normalize lists defensively for legacy inputs.
                key = tuple(resource) if isinstance(resource, list) else resource
                if sender_id not in resource_to_senders[key]:
                    resource_to_senders[key].append(sender_id)

        node_manager = self.node.scheduler.node_manager
        # sender -> list[(resource, sinr_db, desired_or_rssi_dbm)]
        measurements_by_sender = defaultdict(list)
        sinr_values_per_resource = defaultdict(list)

        # Compute each sender/resource SINR once.  Interferers are exactly the
        # other senders occupying that resource, as in the original code.
        for resource, senders in resource_to_senders.items():
            nodes = {sid: node_manager.nodes[sid] for sid in senders if sid in node_manager.nodes}
            for sender_id in senders:
                sender_node = nodes.get(sender_id)
                if sender_node is None:
                    continue
                interfering_nodes = [nodes[sid] for sid in senders if sid != sender_id and sid in nodes]
                sinr, rssi = self.calculate_sinr(self.node, sender_node, interfering_nodes)
                rec = (resource, sinr, rssi)
                measurements_by_sender[sender_id].append(rec)
                sinr_values_per_resource[resource].append((sender_id, sinr, rssi))

        node_validity = {}
        for sender_id in sender_order:
            sender_measurements = measurements_by_sender.get(sender_id, [])
            if not sender_measurements:
                continue
            last_resource = sender_measurements[-1][0]

            if self.common_full_enabled() and self.nr_reception is not None:
                effective_sinr = min(x[1] for x in sender_measurements)
                desired_power = min(x[2] for x in sender_measurements)
                receive_time, packet, _ = packet_by_sender[sender_id]
                success, pssch_bler, _ = self.nr_reception.decode_pssch(
                    effective_sinr, desired_power_dbm=desired_power,
                    sender_id=sender_id, receiver_id=self.node.node_id,
                    packet_id=packet.get('data'), timestamp=receive_time,
                )
                node_validity[sender_id] = {
                    'success': 1 if success else 0,
                    'max_sinr': effective_sinr,
                    'best_rssi': desired_power,
                    'resource': last_resource,
                    'pssch_bler': pssch_bler,
                }
            else:
                all_resources_valid = True
                max_sinr = float('-inf')
                best_rssi = float('-inf')
                for resource, sinr, rssi in sender_measurements:
                    entries = sinr_values_per_resource[resource]
                    max_sinr_on_resource = max(e[1] for e in entries)
                    max_sinr = max(max_sinr, sinr)
                    best_rssi = max(best_rssi, rssi)
                    if sinr < self.SINR_threshold_dB or rssi <= self.RSSI_Margin_dB or sinr < max_sinr_on_resource:
                        all_resources_valid = False
                        break
                node_validity[sender_id] = {
                    'success': 1 if all_resources_valid else 0,
                    'max_sinr': max_sinr,
                    'best_rssi': best_rssi,
                    'resource': last_resource,
                }

        # CORE/FULL sensing observes SCI independently of PSSCH success.
        if self.common_core_enabled():
            for sender_id, node_info in node_validity.items():
                receive_time, packet, resources = packet_by_sender[sender_id]
                sender_node = node_manager.nodes[sender_id]
                self._nr_observe_sci(receive_time, packet, resources, node_info['max_sinr'], sender_node)

        csv_rows = []
        for sender_id in sender_order:
            node_info = node_validity.get(sender_id)
            if node_info is None:
                continue
            receive_time, packet, resources = packet_by_sender[sender_id]
            success = node_info['success']
            best_rssi = node_info['best_rssi']
            sender_node = node_manager.nodes[sender_id]
            _dist = self.calculate_distance(self.node.position, sender_node.position)
            _log_max = float(os.environ.get('SORA_RX_LOG_MAX_DISTANCE_M', 'inf'))
            if _dist <= _log_max:
                csv_rows.append({
                    'timestamp': receive_time,
                    'sender_id': sender_id,
                    'receiver_id': self.node.node_id,
                    'packet_id': packet['data'],
                    'distance': _dist,
                    'success': success,
                })

            if success:
                self.process_message(packet, resources, receive_time)
            elif best_rssi > self.RSSI_Margin_dB and self.protocol in [2, 1]:
                for res in resources:
                    res_tuple = tuple(res) if isinstance(res, list) else res
                    highest_id = self.handle_collision(self.node.prat, res_tuple)
                    if isinstance(highest_id, list):
                        highest_id = tuple(highest_id)
                    elif highest_id is None:
                        highest_id = -1
                    self.collided_resources.add((res_tuple, highest_id))

        if csv_rows:
            write_header = not os.path.exists(self.filename)
            with open(self.filename, 'a', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=['timestamp', 'sender_id', 'receiver_id', 'packet_id', 'distance', 'success'])
                if write_header:
                    writer.writeheader()
                writer.writerows(csv_rows)

        self.receive_buffer.clear()


    # def process_received_messages_1(self):
    #     """Process received messages using refactored helper functions."""
    #     self.RSSI_Margin_dB = self.new_P_s_dB
    
    #     # Group messages by receive time
    #     messages_by_time = group_messages_by_time(self.receive_buffer)
    
    #     for receive_time, messages in messages_by_time.items():
    #         # Group messages by overlapping resources
    #         msg_groups = group_messages_by_resources(messages)
    
    #         for msg_group in msg_groups:
    #             messages_in_group = msg_group['messages']
    #             sender_ids = [packet['sender'] for packet, _ in messages_in_group]
    #             node_manager = self.node.scheduler.node_manager
    
    #             # Precompute sender nodes
    #             sender_nodes = {sid: node_manager.nodes[sid] for sid in sender_ids}
    
    #             # Calculate SINR and RSSI for each message
    #             sinr_values = calculate_sinr_for_messages(self.node, sender_nodes, messages_in_group)
    #             sinr_values.sort(reverse=True, key=lambda x: x[0])
    #             best_sinr, best_rssi, best_packet, best_resources = sinr_values[0]
    
    #             # Write SINR data to CSV (optional logging)
    #             write_sinr_to_csv(self.filename, sinr_values, receive_time, self.node, sender_nodes)
    
    #             # Process the best message
    #             if best_sinr >= self.SINR_threshold_dB and best_rssi > self.RSSI_Margin_dB:
    #                 self.process_message(best_packet, best_resources, receive_time)
    #             elif best_rssi > self.RSSI_Margin_dB:
    #                 # Handle collisions if RSSI is strong but SINR is low
    #                 if self.protocol in [1, 2]:
    #                     for res in best_resources:
    #                         res_tuple = tuple(res) if isinstance(res, list) else res
    #                         highest_id = self.handle_collision(self.node.prat, res_tuple)
    #                         if isinstance(highest_id, list):
    #                             highest_id = tuple(highest_id)
    #                         elif highest_id is None:
    #                             highest_id = -1
    #                         self.collided_resources.add((res_tuple, highest_id))
    #                 else:
    #                     self.process_message_RSSI(best_packet, best_resources, receive_time)
    #             else:
    #                 logger.info(f"Time {self.node.scheduler.current_time}ms: Node {self.node.node_id} discarded all messages due to low SINR.")
    
    #     # Clear the receive buffer
    #     self.receive_buffer.clear()

    
  
        
    def process_message_RSSI(self, packet, resources, send_time):
        sci2_header = packet['sci2']
        sender_id = sci2_header['node_id']
        sender_position = tuple(sci2_header['position'])  # Convert position to tuple
        current_resources = sci2_header['resources']


        for resource in sci2_header['resources']:
            slot, channel = resource
            if self.periodic:

                self.resource_allocation.update_resources(slot, [channel], True)
    
        
    def process_message(self, packet, resources, send_time):
        sci2_header = packet['sci2']
        sender_id = sci2_header['node_id']
        reservation_time=sci2_header['reservation_time']
        sender_position = tuple(sci2_header['position'])  # Convert position to tuple
        current_resources = sci2_header['resources']
        future_resources = sci2_header['future_resource']
        switching_probability = sci2_header['switching_probability']
        staying_probability = 1 - switching_probability
        collided_resources = packet['collided_resources']
        received_RS = packet.get('RS', {})

        if self.sora_core_enabled():
            if packet.get('RS_wire') is not None:
                try:
                    decoded_mode, decoded_method, decoded_states = decode_rs(
                        packet['RS_wire'],
                        rri_s=self.rri,
                        numerology=int(os.environ.get("NRV2X_NUMEROLOGY", "0")),
                        bandwidth_mhz=int(round(self.bandwidth_mhz)),
                    )
                    received_RS = decoded_states
                    self.sora_rs_codec_decode_ok += 1
                    # FULL/SNAPSHOT share the FULL wire mode. Hybrid DELTA must
                    # agree with RS_meta; a mismatch invalidates the RS update.
                    _meta = packet.get('RS_meta')
                    if isinstance(_meta, dict):
                        expected_mode = str(_meta.get('mode', 'full')).lower()
                        if expected_mode == 'snapshot':
                            expected_mode = 'full'
                        if decoded_mode != expected_mode:
                            raise ValueError(
                                f"RS wire/meta mode mismatch: wire={decoded_mode}, meta={expected_mode}"
                            )
                except Exception:
                    received_RS = {}
                    self.sora_rs_codec_decode_errors += 1
            if received_RS is None:
                received_RS = {}
            sender_node = self.node.scheduler.node_manager.nodes[sender_id]
            distance = self.calculate_distance(self.node.position, sender_node.position)
            rs_status = {tuple(k): int(v) for k, v in received_RS.items()}
            rs_valid = True
            rs_meta = packet.get('RS_meta')
            if self.sora_rs_mode == "hybrid":
                if not isinstance(rs_meta, dict) or rs_meta.get("mode") not in ("full", "delta"):
                    rs_status, rs_valid = {}, False
                else:
                    reconstructed, rs_valid = self.sora_rs_rx.ingest(
                        sender_id,
                        HybridRSPacket(
                            mode=str(rs_meta["mode"]), epoch=int(rs_meta["epoch"]),
                            seq=int(rs_meta["seq"]), states=rs_status,
                        ),
                    )
                    if rs_valid and reconstructed is not None:
                        # The SORA propagation rule consumes only ONE_HOP entries
                        # from a neighbor's reconstructed map. Collision feedback
                        # additionally needs explicit values for this UE's own
                        # current resource(s), including FREE=0. Passing all 500
                        # positions on every delta is semantically redundant and
                        # makes long dense runs unnecessarily expensive.
                        rs_status = {
                            tuple(k): int(v) for k, v in reconstructed.items()
                            if int(v) == int(ResourceState.ONE_HOP)
                        }
                        for _own_res in self.current_resources:
                            _key = tuple(_own_res)
                            rs_status[_key] = int(reconstructed.get(_key, int(ResourceState.FREE)))
                    else:
                        rs_status = {}
                        self.sora_rs_invalid_delta_rx += 1
            self.sora_engine.ingest_report(SORAReport(
                timestamp_s=self.node.scheduler.current_time,
                sender_id=sender_id,
                current_resources=tuple(tuple(x) for x in current_resources),
                future_resources=tuple(tuple(x) for x in future_resources),
                resource_status=rs_status,
                distance_m=distance,
                rs_valid=rs_valid,
            ))
        else:
            self.num_receive_buffer_CRA6G.append((self.node.scheduler.current_time,sender_id, current_resources, future_resources, received_RS) )
        
        

        #self.resource_allocation.update_RS_grid(sender_id, current_resources, future_resources, self.node.scheduler.current_time, received_RS, 1)
                # Update the RS grid with sender's resources and received RS
        # if self.node.node_id=='veh487':
        #     print( " node_id", self.node.node_id, "sender", sender_id, "current_resources", current_resources , self.resource_allocation.RS_grid)
        #self.reevaluate_future_resources()

        # if self.node.node_id==100:
        #    # print(sender_id,len(self.node.neighbor_table))
        #     print(self.node.scheduler.current_time,sender_id, sci2_header['resources'])
        
        if self.protocol!=3:
            for resource in sci2_header['resources']:
                slot, channel = resource
                if self.periodic:
                    
                    self.resource_allocation.update_resources(slot, [channel], True)
    

        sender = packet['sender']
        logger.info(f"Time {self.node.scheduler.current_time}ms: Node {self.node.node_id} received from node {sender}")
        self.neighbor_resources[sender] = sci2_header['resources']
        self.node.received_packets_from_neighbors += 1
        self.node.reception_times.append({'time': send_time, 'sender': sender})
        self.node.received_packets[sender][self.node.node_id].append(send_time)
    
           
        self.node.update_neighbor_table(sender, sci2_header['resources'], send_time)
    
        # Call the new update_prat function to handle PRAT updates
        if not self.sora_core_enabled():
            self.update_prat(sender_id, sender_position, current_resources, future_resources, switching_probability, staying_probability)
        if collided_resources:
            if self.protocol in [1,2]:
                if self.num_per_time < self.node.scheduler.current_time:
                    self.num_receive_buffer.append((self.node.scheduler.current_time, sender_id, collided_resources))
                    # self.node.scheduler.schedule_event(self.node.scheduler.current_time + self.num_receive_timer, Event(self.node.scheduler.current_time + self.num_receive_timer, self.action_process_received_messages))
                    self.num_per_time = self.node.scheduler.current_time  
                else:
                    self.num_receive_buffer.append((self.node.scheduler.current_time, sender_id, collided_resources))

    def action_process_received_messages_CRA6G(self):
        """
        Process the received RS grid to count occurrences of H, L, 2, and 1 for each slot and channel.
        Create and update totalRS using the highest count value for each slot-channel pair.
        For each current and future resource, add a field for distance to store the highest distance for each resource.
        """
        # Initialize dictionaries for counts and distances
        rs_cumulative_counts = {}
        self.totalRS = {}
        self.distanceRS = {}  # Maintain the same structure as totalRS
    
        percentage_threshold_for_1 = 0.01
        percentage_threshold_for_2 = 0.1
        percentage_threshold_for_H = 0.2
        percentage_threshold_for_L = 0.1
    
        # Iterate through the receive buffer to process received RS
        for receive_time, sender_id, current_resources, future_resources, received_RS in self.num_receive_buffer_CRA6G:
            
            # Calculate distance between sender and receiver
            sender_node = self.node.scheduler.node_manager.nodes[sender_id]
            sender_position = sender_node.position
            receiver_position = self.node.position
            distance = self.calculate_distance(receiver_position, sender_position)
            
            # Process received_RS for counting and distances
            for (slot, channel), received_status in received_RS.items():
                # Initialize counts and distances for slot-channel if not already present
                if slot not in rs_cumulative_counts:
                    rs_cumulative_counts[slot] = {}
                if channel not in rs_cumulative_counts[slot]:
                    rs_cumulative_counts[slot][channel] = {'H': 0, 'L': 0, '2': 0, '1': 0}
                if slot not in self.distanceRS:
                    self.distanceRS[slot] = {}
                if channel not in self.distanceRS[slot]:
                    self.distanceRS[slot][channel] = 0  # Initialize with 0
    
                # Update counts based on the received status
                if received_status == self.resource_allocation.H:
                    rs_cumulative_counts[slot][channel]['H'] += 1
                elif received_status == self.resource_allocation.L:
                    rs_cumulative_counts[slot][channel]['L'] += 1
                elif received_status == 2:
                    rs_cumulative_counts[slot][channel]['2'] += 1
                elif received_status == 1:
                    rs_cumulative_counts[slot][channel]['1'] += 1
                
                # Update distance for the resource (store the maximum distance)
                self.distanceRS[slot][channel] = max(self.distanceRS[slot][channel], distance)
    
        # Build totalRS from rs_cumulative_counts
        for slot, channels in rs_cumulative_counts.items():
            for channel, counts in channels.items():
                # Calculate total counts for this slot-channel
                total_count = sum(counts.values())
    
                # Skip if no counts exist
                if total_count == 0:
                    continue
                    
                       # Calculate percentages for each status
                percentages = {status: count / total_count for status, count in counts.items()}
                # if self.node.node_id == 152:
                #     print(f"slot, channel- {slot},{channel}  - percentages: {percentages}")
    
                if (slot, channel) in self.current_resources:
                     # Check if `1` has a priority based on its percentage
                    if self.node.node_id=='a':
                        print(f"Slot {slot}, Channel {channel}, {percentages.get('1', 0)},  Percentages: {percentages}")
                    if percentages.get('1', 0) <= .8 and self.rev_RC>0:
                        self.RC = 0
                        if self.node.node_id=='a':    
                            print(f"reset RC, Percentages: {percentages}")

                # Determine max_status based on priority
                if percentages.get('1', 0) >= percentage_threshold_for_1:
                    max_status = '1'
                elif percentages.get('L', 0) >= percentage_threshold_for_L:
                    max_status = 'L'
                elif percentages.get('2', 0) >= percentage_threshold_for_2:
                    max_status = '2'
                elif counts.get('H', 0) > 0:
                    max_status = 'H'

                else:
                    # Fallback to the status with the highest count
                    max_status = 'H'
                
                # Assign max_status_value based on the determined max_status
                max_status_value = (
                    self.resource_allocation.H if max_status == 'H' else
                    self.resource_allocation.L if max_status == 'L' else
                    int(max_status)  # Convert '1' or '2' to integer
)

                # Add to totalRS
                if slot not in self.totalRS:
                    self.totalRS[slot] = {}
                self.totalRS[slot][channel] = max_status_value
    
        # Update totalRS and distances for current and future resources
        for receive_time, sender_id, current_resources, future_resources, _ in self.num_receive_buffer_CRA6G:
            # Calculate distance between sender and receiver
            sender_node = self.node.scheduler.node_manager.nodes[sender_id]
            sender_position = sender_node.position
            receiver_position = self.node.position
            distance = self.calculate_distance(receiver_position, sender_position)
    
            # Process future resources
            for slot, channel in future_resources:
                if slot not in self.totalRS:
                    self.totalRS[slot] = {}
                if slot not in self.distanceRS:
                    self.distanceRS[slot] = {}
                if self.totalRS[slot].get(channel) not in [1]:
                    self.totalRS[slot][channel] = 'DF'  # Set the value to H for future resources
                self.distanceRS[slot][channel] =  distance
    
            # Process current resources
            for slot, channel in current_resources:
                if slot not in self.totalRS:
                    self.totalRS[slot] = {}
                if slot not in self.distanceRS:
                    self.distanceRS[slot] = {}
                self.totalRS[slot][channel] = 'DC'  # Set the value to 1 for current resources
                self.distanceRS[slot][channel] = distance
    
        # Convert self.totalRS to a flat dictionary
        flat_totalRS = {}
        for slot, channels in self.totalRS.items():
            for channel, status in channels.items():
                flat_totalRS[(slot, channel)] = {
                    'status': status,
                    'distance': self.distanceRS[slot][channel]  # Add the distance for each resource
                }
    
        # Update the RS grid with the new totalRS
        self.resource_allocation.update_recived_RS_grid(self.node.scheduler.current_time, flat_totalRS)
        # if self.node.node_id == 152:
        #     print(f"RS_grid: {self.resource_allocation.RS_grid}")
            #print(self.num_receive_buffer_CRA6G )

        # Clear the receive buffer
        self.num_receive_buffer_CRA6G = []



        





    def action_process_received_messages(self):
        """Process received collision messages and update resource allocations."""
        received_collision_count = 0
        id_s = []
        res_s = []
    
        # Iterate through the receive buffer to check for resource overlap
        for receive_time, sender_id, collided_resources in self.num_receive_buffer:
            overlap_count, overlap_ids, overlap_res = check_resource_overlap(self.current_resources, collided_resources)
            received_collision_count += overlap_count
            id_s.extend(overlap_ids)
            res_s.extend(overlap_res)
    
        # Maintain the neighbor table
        self.node.maintain_neighbor_table()
        neighbor_table_length = len(self.node.neighbor_table)
    
        # Calculate collision density
        collision_density = calculate_collision_density(received_collision_count, neighbor_table_length, self.num_channels)
    
        # Get the most common attached ID
        most_common_element = get_most_common_id(id_s)
    
        # Check if collision density exceeds the threshold
        if collision_density > self.ROI_Z:
            if self.protocol in [1]:
                # Reset the resource counter (RC) for Protocol 1
                self.RC = 0
            elif self.protocol == 2:
                # Reset the RC counter if the most common ID does not match the current node's ID
                if most_common_element != self.node.node_id:
                    for res in self.current_resources:
                        slot, channel = res
                        self.resource_allocation.update_resources(slot, [channel], True)
                    self.RC = -1
    
        # Clear the receive buffer
        self.num_receive_buffer = []



    def identify_vehicles_involved(self, prat, resource):
        """
        Identify vehicles involved in the collision on the given resource.
        This function will return two lists:
            - vehicles_current: Vehicles currently using the resource.
            - vehicles_future: Vehicles that have this resource as their future resource.
        """
        vehicles_current = []
        vehicles_future = []
        
        # Check PRAT to find vehicles with the given resource
        if resource in prat:
            # Check 'U' for vehicles using it as current resource
            vehicles_current = [(entry[0], 'current') for entry in prat[resource]['U']]
            
            # Check 'A' for vehicles using it as future resource
            vehicles_future = [(entry[0], 'future') for entry in prat[resource]['A']]
        
        return vehicles_current, vehicles_future

  

    
    def remove_vehicles_using_different_current_resource(self, prat, current_vehicles, future_vehicles, resource):
        """
        Remove vehicles that are no longer using the resource as their current resource.
        This function returns an updated list of vehicles involved in the collision.
        Now it also checks if the vehicle is using a different slot for the resource.
        """
        remaining_vehicles = []
        
        # Extract the slot and channel from the resource to be checked
        resource_slot, resource_channel = resource
        
        # Go through each vehicle in the future_vehicles list
        for vehicle_id, usage_type in future_vehicles:
            is_using_different_resource = False
            
            # Check if the vehicle is using a different slot or resource
            for res, res_data in prat.items():
                res_slot, res_channel = res  # Separate slot and channel of the current resource being checked
    
                if res_slot != resource_slot:  # Check if it's a different slot
                    for entry in res_data['U']:
                        if entry[0] == vehicle_id:
                            is_using_different_resource = True
                            break
                # elif res_channel != resource_channel:  # Check if it's a different channel
                #     for entry in res_data['U']:
                #         if entry[0] == vehicle_id:
                #             is_using_different_resource = True
                #             break
                if is_using_different_resource:
                    break
            
            # Add the vehicle to the remaining list if it is still using the same slot and resource
            if not is_using_different_resource:
                remaining_vehicles.append((vehicle_id, usage_type))
        
        return remaining_vehicles

        
    def evaluate_remaining_vehicles(self, remaining_vehicles):
        """
        Evaluate the remaining vehicles to determine which vehicle should continue using the resource.
        The vehicle with the highest ID will continue, and others will be notified to switch.
        """
        if not remaining_vehicles:
            return None  # No vehicles left, recommend switching
        
        # Sort vehicles by ID (highest first)
        remaining_vehicles.sort(reverse=True, key=lambda x: x[0])
        
        if len(remaining_vehicles) == 1:
            return remaining_vehicles[0][0]  # Return the highest vehicle ID
        
        # The vehicle with the highest ID continues
        vehicle_to_continue = remaining_vehicles[0][0]
        
        return vehicle_to_continue  # Return a single ID
        
    def handle_collision(self, prat, resource ):
        """
        Handle a detected collision on a given resource.
        This function will:
        - Identify vehicles involved.
        - Remove vehicles that are no longer using the resource.
        - Determine which vehicle should continue and which should switch.
        """
        # Step 1: Identify vehicles involved in the collision
        vehicles_current, vehicles_future = self.identify_vehicles_involved(prat, resource)

        all_vehicles = vehicles_current + vehicles_future

        remaining_vehicles = self.remove_vehicles_using_different_current_resource(prat, vehicles_current, vehicles_future, resource)

        vehicle_to_continue = self.evaluate_remaining_vehicles(remaining_vehicles)
        


        
        return vehicle_to_continue
    
    
    
    def update_prat(self, sender_id, sender_position, current_resources, future_resources, switching_probability, staying_probability):
        """
        Update the PRAT for the current and future resources based on the sender's ID and position.
        """
        # Update PRAT for each resource in current_resources
        for current_resource in current_resources:
            if current_resource not in self.node.prat:
                self.node.prat[current_resource] = {'U': [], 'A': [], 'highest_u_id': None, 'highest_a_id': None}
            
            # Update or add entry in U
            u_entry_index = next((i for i, entry in enumerate(self.node.prat[current_resource]['U']) if entry[0] == sender_id), None)
            if u_entry_index is not None:
                # If the entry exists, update it
                self.node.prat[current_resource]['U'][u_entry_index] = (sender_id, sender_position, self.node.scheduler.current_time)
            else:
                # Otherwise, append a new entry
                self.node.prat[current_resource]['U'].append((sender_id, sender_position, self.node.scheduler.current_time))
            
            # Recalculate highest_u_id after the update
            self.node.prat[current_resource]['highest_u_id'] = max((entry[0] for entry in self.node.prat[current_resource]['U']), default=None)
    
            # Update or add entry in A for current resource with staying probability
            a_entry_index = next((i for i, entry in enumerate(self.node.prat[current_resource]['A']) if entry[0] == sender_id), None)
            if a_entry_index is not None:
                # If the entry exists, update it
                self.node.prat[current_resource]['A'][a_entry_index] = (sender_id, sender_position, staying_probability, self.node.scheduler.current_time)
            else:
                # Otherwise, append a new entry
                self.node.prat[current_resource]['A'].append((sender_id, sender_position, staying_probability, self.node.scheduler.current_time))
        
            # Recalculate highest_a_id after the update for the current resource
            self.node.prat[current_resource]['highest_a_id'] = max((entry[0] for entry in self.node.prat[current_resource]['A']), default=None)
    
        # Update A_r_i for future predictions
        for future_resource in future_resources:
            if future_resource not in self.node.prat:
                self.node.prat[future_resource] = {'U': [], 'A': [], 'highest_u_id': None, 'highest_a_id': None}
            
            # Update or add entry in A
            a_entry_index = next((i for i, entry in enumerate(self.node.prat[future_resource]['A']) if entry[0] == sender_id), None)
            if a_entry_index is not None:
                # If the entry exists, update it
                self.node.prat[future_resource]['A'][a_entry_index] = (sender_id, sender_position, switching_probability, self.node.scheduler.current_time)
            else:
                # Otherwise, append a new entry
                self.node.prat[future_resource]['A'].append((sender_id, sender_position, switching_probability, self.node.scheduler.current_time))
            
            # Recalculate highest_a_id after the update
            self.node.prat[future_resource]['highest_a_id'] = max((entry[0] for entry in self.node.prat[future_resource]['A']), default=None)
             


    def receive_packet(self, packet, resources):
        if self.per_time < self.node.scheduler.current_time:
            self.receive_buffer.append((self.node.scheduler.current_time, packet, resources))
            self.node.scheduler.schedule_event(self.node.scheduler.current_time + self.receive_timer, Event(self.node.scheduler.current_time + self.receive_timer, self.process_received_messages))
            self.per_time = self.node.scheduler.current_time  
        else:
            self.receive_buffer.append((self.node.scheduler.current_time, packet, resources))

    def sensing_window(self):
        sensed_resources = []
        for neighbor, resources in self.neighbor_resources.items():
            sensed_resources.extend(resources)
        self.sensed_resources = sensed_resources[:5]
        for res in self.sensed_resources:
            slot, channel = res
            self.resource_allocation.update_resources(slot, [channel], True)
