# Final-manuscript configuration audit

**Paper:** “Decentralized Collision Mitigation for Reliable Broadcast Communication in Safety-Critical Vehicular Networks”

This restricted repository has been frozen against the final manuscript supplied as `SORA(6).pdf`. It contains only the detailed-PHY / SUMO reproduction path for SORA and the NR-V2X Mode 2 baseline.

## Manuscript-to-release audit

| Item | Final manuscript | Release configuration | Status |
|---|---|---|---|
| Detailed evaluation | Full-population SUMO + detailed vehicular PHY | `profile=full`, `phy_variant=strict`, frozen SUMO traces | MATCH |
| Detailed duration | 30 s per scenario | `duration_s=30.0` | MATCH |
| Carrier | 5.89 GHz | `carrier_frequency_ghz=5.89` and explicit CLI argument | MATCH |
| Bandwidth | 10 MHz | `bandwidth_mhz=10` | MATCH |
| Numerology | 0 | `numerology=0` | MATCH |
| RRI | 100 ms | `rri_s=0.1` | MATCH |
| Subchannel | 10 PRBs | `subchannel_size_prb=10` | MATCH |
| Pool size | 5 subchannels/slot, 500 resource positions/RRI | frozen config and simulator sizing | MATCH |
| MCS | MCS 11 for detailed-PHY figures | `mcs=11` | MATCH |
| Tx power | 23 dBm total UE power | `tx_power_dbm=23`, `tx_power_mode=total` | MATCH |
| Noise figure | 9 dB | `noise_figure_db=9` | MATCH |
| Sensing threshold | -90 dBm | `sensing_threshold_dbm=-90` | MATCH |
| SORA collision thresholds | theta=0.8, theta_l=0.9 | `0.8`, `0.9` | MATCH |
| Candidate floor | 5%, +3 dB relaxation | `map5`, 5%, +3 dB | MATCH |
| NR RC | 5-15 at 100-ms RRI | standard NR RC | MATCH |
| SORA RC | 50-150 | factor-10 RC relative to the NR 5-15 range | MATCH |
| Standby resource | advertised to direct one-hop neighbors only; not relayed as two-hop state | advertisement enabled; direct-future store is separate from the propagated 0/1/2 RS map | MATCH |
| Hybrid RS | periodic FULL + DELTA | `rs_mode=hybrid` | MATCH |
| DELTA reference | differences relative to the current FULL reference | FULL-anchored transmitter/receiver reconstruction | MATCH |
| FULL interval | 1 s | `rs_full_interval_s=1.0` | MATCH |
| RS encoding | Golomb-Rice coding of ordered index gaps | `rice_gap`, real encode/decode path enabled | MATCH |
| Application payload | 300 B | `application_bytes=300` | MATCH |
| SORA transmitted packet | 349 B | `sora_packet_bytes=349` | MATCH |
| NR-V2X transmitted packet | 300 B | `nrv2x_packet_bytes=300` | MATCH |
| SORA packet-level RS/control allowance | 49 B | 349 - 300 = 49 B fixed PHY accounting | MATCH |
| Shadowing | state-dependent, spatially correlated | detailed channel implementation | MATCH |
| Decorrelation distance | 10 m LOS, 13 m NLOSv/NLOS | detailed channel implementation | MATCH |
| Blockage/interference | stochastic vehicle blockage + aggregate co-channel interference | strict detailed PHY | MATCH |
| Half-duplex | applied | channel/MAC reception path | MATCH |

### Reporting precision

The simulator uses the frozen MCS-11 threshold `10.145625 dB`; the manuscript reports this value rounded to `10.1 dB`. This is treated as reporting precision rather than a configuration mismatch.

### Packet accounting and logical RS serialization

The paper fixes the *transmitted SORA packet* at 349 B, i.e. a 300-B application payload plus a 49-B SORA-specific packet-level allowance. The release therefore passes 349 B directly to the SORA PHY/resource-sizing path and 300 B to NR-V2X. `rs_on_air_mode=none` means that the runner must **not add a second RS byte budget on top of the already fixed 349-B SORA packet**.

Separately, the logical FULL/DELTA RS content is serialized and decoded with Golomb-Rice coding so codec correctness and RS state reconstruction can be tested. The logical encoding statistics are recorded independently of the fixed packet-level PHY accounting.

## Analysis detail retained from the figure pipeline

The detailed simulator runs for the full 30 s. The PRR/PIR post-processing discards the first 5 s as initialization warm-up and evaluates the remaining 25 s. This is an analysis implementation detail retained from the original detailed-PHY figure pipeline; it does not shorten the simulator run itself.

PIR is measured directly between consecutive successful receptions for each ordered Tx-Rx pair. A pair's PIR chain is reset when it leaves the 0-500 m evaluation range. The manuscript figures use the measured PIR, not a fitted `RRI/PRR` replacement.

## Frozen detailed cases

The restricted release contains four frozen 30-s mobility traces:

- Highway Low
- Highway High
- Urban Low
- Urban High

The Low/High labels identify distinct SUMO traffic realizations and must not be interpreted as a controlled density-only sweep.

## Important rerun gate

The prior detailed H/L results were produced before all of the final manuscript values above were frozen. In particular, the older runner used 349 B for both protocols, a 0.1-s FULL interval, and a 5.9-GHz channel default.

**Therefore the public repository must not be tagged as the submitted reproducibility release until the four SORA cases and four NR-V2X cases have been rerun from this frozen configuration and the final detailed-PHY figures have been regenerated.**

After that rerun:

1. compare the regenerated detailed figures/500-m values with the final manuscript;
2. update the manuscript values if the scientifically correct rerun changes them;
3. run the complete unit/smoke test suite again;
4. choose the final software license;
5. update `CITATION.cff` version/date;
6. create the public release tag, e.g. `SORA-Paper-Release-v1.0`.
