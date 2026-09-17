# Reproducibility package for **Decentralized Collision Mitigation for Reliable Broadcast Communication in Safety-Critical Vehicular Networks**

This repository is the restricted reproducibility package associated with the paper:

**“Decentralized Collision Mitigation for Reliable Broadcast Communication in Safety-Critical Vehicular Networks”**  
Mahmoud Elsharief and Han-Shin Jo

The repository contains the detailed-PHY communication-simulation implementation used to evaluate **SORA** against the **NR-V2X Mode 2** baseline. It is intentionally focused on the paper-relevant detailed-PHY/SUMO workflow and does not expose unrelated development branches.

The scientific configuration in `configs/paper_full_strict_mcs11.json` is frozen against the final manuscript.

> **Important:** before the repository is publicly tagged, the final detailed H/L cases must be rerun from the frozen configuration and the regenerated values must be checked against the final manuscript. See `AUDIT_STATUS.md`.

## Two reproduction paths

### Option 1 — Test the implementation using the supplied mobility traces

**Purpose:** quickly verify and explore the communication simulator without installing SUMO.  
**SUMO required:** No.

The repository contains four CSV mobility traces. These are ordinary CSV files and can also be opened in Excel:

```text
mobility/frozen/highway_low.csv
mobility/frozen/highway_high.csv
mobility/frozen/urban_low.csv
mobility/frozen/urban_high.csv
```

The simulator reads these files directly using the trace-mobility path. This option is intended for implementation testing, short development runs, checking the detailed-PHY processing, and validating PRR/PIR aggregation and plotting.

Example:

```powershell
python experiments/run_case.py --protocol sora --scenario highway --density low --dry-run
python experiments/run_case.py --protocol sora --scenario highway --density low
```

For a temporary 5-s software test, copy the paper configuration:

```powershell
Copy-Item .\configs\paper_full_strict_mcs11.json .\configs\test_5s.json
notepad .\configs\test_5s.json
```

Change only:

```json
"duration_s": 30.0
```

to:

```json
"duration_s": 5.0
```

Then run, for example:

```powershell
python experiments/run_case.py --protocol sora --scenario highway --density low --config .\configs\test_5s.json
```

A 5-s run is a **software test** and must not be used as a paper result.

### Option 2 — Reproduce the complete paper workflow from SUMO

**Purpose:** reproduce the complete mobility-generation and communication-simulation workflow used for the paper.  
**SUMO required:** Yes.

The complete original SUMO package is distributed separately as a GitHub Release asset:

```text
SUMO.zip
```

It is intentionally kept outside the normal Git repository because it is a large archive.

The end-to-end paper reproduction workflow is:

```text
SUMO.zip
    ↓
Install and configure SUMO
    ↓
Extract the complete SUMO package
    ↓
Run the Highway/Urban SUMO scenarios
    ↓
Generate/export vehicle mobility
    ↓
Convert mobility to the simulator CSV schema
    ↓
Run SORA and NR-V2X separately
    ↓
Aggregate PRR and measured PIR
    ↓
Generate the detailed-PHY manuscript figures
```

The full `SUMO.zip` should be downloaded from the GitHub **Release assets**, not from the normal source tree. After the final archive is supplied, its checksum, exact extraction directory, scenario paths, SUMO commands, and trace-generation commands will be documented in `sumo/README.md`.

## Scope

Included:

- SORA and NR-V2X Mode 2 execution paths required by the detailed system-level PHY;
- highway/urban LOS, NLOSv and NLOS propagation handling;
- state-dependent path loss, spatially correlated shadowing and stochastic vehicle blockage;
- aggregate co-channel interference, thermal noise and half-duplex reception;
- sidelink sensing, candidate selection, RC handling and PSSCH reception abstraction;
- SORA one-hop/two-hop RS processing;
- direct one-hop standby-resource advertisement;
- distributed collision detection and recovery;
- FULL-anchored Hybrid RS reconstruction;
- Golomb-Rice RS encode/decode implementation;
- four CSV mobility traces for direct simulator testing;
- selected SUMO inputs for provenance;
- one-case-at-a-time experiment launcher;
- separate PRR/PIR aggregation;
- easy final figure generation;
- unit tests and a short installation-only smoke test.

## 1. Python environment

Recommended:

- Python 3.11 or newer;
- a normal virtual environment or Conda environment;
- sufficient RAM, disk space and CPU time for full-population detailed-PHY runs.

For **Option 1**, SUMO is not required.  
For **Option 2**, SUMO must also be installed and `SUMO_HOME` configured.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

When launching several independent simulations concurrently:

```powershell
$env:OMP_NUM_THREADS=1
$env:MKL_NUM_THREADS=1
$env:OPENBLAS_NUM_THREADS=1
$env:NUMEXPR_NUM_THREADS=1
```

No Docker image and no paper-wide one-command runner are provided.

## 2. Verify the installation

```powershell
python -m pytest -q
python tests/run_smoke.py
```

The smoke test is an installation/implementation check only; it does not reproduce a paper result.

## 3. Final detailed-PHY configuration

The authoritative machine-readable configuration is:

```text
configs/paper_full_strict_mcs11.json
```

Important manuscript-frozen values include:

```text
Detailed PHY profile            full / strict
MCS                             11
Simulation duration             30 s
Carrier                         5.89 GHz
Bandwidth                       10 MHz
Numerology                      0
RRI                             100 ms
Subchannel size                 10 PRBs
Subchannels per slot            5
Resource positions per RRI      500
Tx power                        23 dBm total
Noise figure                    9 dB
Sensing threshold               -90 dBm
SORA theta                      0.8
SORA theta_l                    0.9
NR-V2X RC                       5-15
SORA RC                         50-150
SORA candidate floor            5%, +3 dB relaxation
Hybrid FULL interval            1 s
RS encoding                     Golomb-Rice gap coding
Standby advertisement           direct one-hop only
Application payload             300 B
SORA transmitted packet         349 B
NR-V2X transmitted packet       300 B
```

The 349-B SORA packet already contains the fixed 49-B SORA-specific RS/control allowance. The runner therefore does not append an additional dynamic RS byte budget on top of 349 B.

## 4. Run one communication experiment at a time

`experiments/run_case.py` launches exactly one protocol/scenario/traffic case.

Inspect a command:

```powershell
python experiments/run_case.py --protocol sora --scenario highway --density high --dry-run
```

Run SORA:

```powershell
python experiments/run_case.py --protocol sora --scenario highway --density high
```

Run the matching NR-V2X baseline separately:

```powershell
python experiments/run_case.py --protocol nr --scenario highway --density high
```

The detailed H/L matrix is:

```text
highway / low
highway / high
urban   / low
urban   / high
```

Both protocols are run separately for each mobility case.

Raw outputs are written to:

```text
results/raw/<scenario>/<density>/<protocol>/seed_<seed>/
```

Each completed case retains its reception CSV, log and exact command line.

## 5. Aggregate PRR and measured PIR

Example:

```powershell
python analysis/aggregate_case.py `
  --input results/raw/highway/high/sora/seed_14/receptions.csv `
  --output results/metrics/highway/high/sora/seed_14/prr_pir.csv
```

The detailed simulator runs for 30 s. The figure pipeline excludes the first 5 s as initialization warm-up and evaluates the remaining 25 s.

PRR is calculated from pooled reception opportunities in the 0-500 m evaluation range. PIR is measured directly from consecutive successful receptions for each ordered Tx-Rx pair.

## 6. Generate the detailed manuscript figures

Once the required metric CSVs exist:

```powershell
python analysis/plot_paper_figures.py
```

This generates PDF and PNG versions of:

```text
Fig18_highway_prr
Fig19_highway_pir
Fig20_urban_prr
Fig21_urban_pir
```

The plotting stage is intentionally simple once valid simulation results have been produced.

## Repository layout

```text
simulator/     detailed communication-simulation implementation
configs/       frozen manuscript configuration
experiments/   one-case experiment launcher
mobility/      CSV traces for direct testing/replay
sumo/          SUMO documentation and selected provenance inputs
analysis/      PRR/PIR aggregation and figure generation
tests/         unit tests and installation smoke test
results/       empty in the public release
figures/       generated locally
```

The complete large mobility-generation package is distributed separately as the GitHub Release asset `SUMO.zip`.

## Reproducibility policy

The package supports two levels of use:

1. **Implementation testing from supplied CSV traces** — easy to start and does not require SUMO.
2. **Complete paper-workflow reproduction from `SUMO.zip`** — requires SUMO, mobility generation/export, communication simulation, aggregation and plotting.

The second workflow is the intended end-to-end reproduction route for the paper **“Decentralized Collision Mitigation for Reliable Broadcast Communication in Safety-Critical Vehicular Networks.”**

## Public-release gate

Do not create the final paper-release tag until:

1. the final eight detailed H/L protocol runs have been completed using the manuscript-frozen configuration;
2. the four detailed figures have been regenerated and checked against the final manuscript;
3. the complete `SUMO.zip` has been inspected and its exact extraction/regeneration procedure documented;
4. the `SUMO.zip` SHA-256 checksum has been recorded;
5. all unit and smoke tests pass;
6. the final software license is selected;
7. `CITATION.cff` is updated from release-candidate status;
8. the repository is checked to ensure that no generated result CSVs or private development artifacts are included.
