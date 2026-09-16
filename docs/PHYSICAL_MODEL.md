# Detailed system-level PHY used in the release

The restricted release contains the detailed-PHY path used for the final full-population SUMO evaluation. The main channel/reception components are implemented under `simulator/channel.py` and `simulator/nrv2x/`.

## Frozen radio configuration

The paper-release configuration uses:

- 5.89-GHz carrier frequency;
- 10-MHz channel bandwidth;
- numerology 0;
- 100-ms RRI;
- 10-PRB subchannels;
- five subchannels per slot and 500 time-frequency resource positions per RRI;
- MCS 11 for the detailed-PHY manuscript figures;
- 23-dBm total UE transmit power;
- 9-dB receiver noise figure;
- -90-dBm sensing threshold;
- threshold-based PSSCH reception using the frozen MCS-11 system-level SINR threshold.

`configs/paper_full_strict_mcs11.json` is authoritative for the numeric experiment configuration.

## Propagation and reception effects

The strict detailed path includes:

- LOS, vehicle-blocked NLOSv and urban NLOS states;
- state-dependent V2V path loss following the implemented 3GPP TR 37.885 large-scale model;
- zero-mean log-normal shadow fading with state/scenario-dependent standard deviations;
- spatially correlated shadowing with 10-m LOS and 13-m NLOSv/NLOS decorrelation distances;
- stochastic NLOSv vehicle-blockage loss;
- aggregate co-channel interference from simultaneous overlapping transmissions;
- thermal noise at the per-subchannel bandwidth;
- total UE power split across occupied subchannels when more than one subchannel is used;
- half-duplex reception failures;
- sidelink sensing/SCI observations, candidate exclusion and PSSCH reception constraints.

The four detailed manuscript curves use frozen full-population SUMO-derived Highway Low/High and Urban Low/High traces. Exact replay uses the CSV files under `mobility/frozen/`; live SUMO regeneration is an optional separate workflow.

## SORA-specific packet accounting

The application payload is 300 B. SORA is evaluated with a fixed 349-B transmitted packet, providing a 49-B packet-level allowance for SORA-specific RS/control information. NR-V2X transmits the same 300-B application payload without the SORA-specific allowance.

Logical SORA FULL/DELTA content is serialized with Golomb-Rice gap coding and reconstructed at the receiver. FULL updates establish the epoch reference and DELTA updates are relative to that FULL reference. The periodic FULL refresh interval is 1 s.

The standby resource is advertised only to the directly receiving one-hop neighbor and is stored separately from the ordinary 0/1/2 RS map, so it is not forwarded as two-hop occupancy state.
