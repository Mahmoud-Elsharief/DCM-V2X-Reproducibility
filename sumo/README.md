# SUMO Mobility Reproduction

This directory documents the mobility workflow associated with the paper:

**Decentralized Collision Mitigation for Reliable Broadcast Communication in Safety-Critical Vehicular Networks**

The reproducibility package supports two different workflows.

## Option 1 - Test the communication simulator using supplied traces

SUMO is **not required** for this option.

The repository already provides the mobility traces used as direct inputs to the communication simulator:

```text
mobility/frozen/highway_low.csv
mobility/frozen/highway_high.csv
mobility/frozen/urban_low.csv
mobility/frozen/urban_high.csv
```

These CSV files can also be opened with spreadsheet software such as Microsoft Excel.

This option is intended for:
- checking the SORA and NR-V2X implementations;
- running short development tests;
- verifying PRR/PIR aggregation;
- checking the plotting pipeline.

Example:

```powershell
python experiments/run_case.py --protocol sora --scenario highway --density low
```

No SUMO installation is needed.

---

## Option 2 - Reproduce the complete mobility + communication workflow

For full end-to-end reproduction, download the complete SUMO archive from the GitHub Release associated with this repository:

```text
SUMO.zip
```

The archive contains the original SUMO networks, traffic files, configurations, trace-generation notebooks, and archived generated mobility data.

### Verify the archive

Expected SHA-256:

```text
FFDF39901B079A9492EBC2BECD750F3DCAA51AE5627A847487B3033FC5A02F8F
```

On Windows PowerShell:

```powershell
Get-FileHash .\SUMO.zip -Algorithm SHA256
```

The value must match the checksum above.

---

## SUMO version

The supplied scenarios were created using:

```text
Eclipse SUMO 1.20.0
```

Using the same SUMO version is recommended for mobility regeneration.

After installing SUMO, configure `SUMO_HOME`. For example:

```powershell
$env:SUMO_HOME="C:\Program Files (x86)\Eclipse\Sumo"
$env:PATH="$env:SUMO_HOME\bin;$env:PATH"
$env:PYTHONPATH="$env:SUMO_HOME\tools;$env:PYTHONPATH"
```

Adjust the installation path if SUMO is installed elsewhere.

Verify:

```powershell
sumo --version
python -c "import traci; print('TraCI available')"
```

---

## Extract the complete archive

Example:

```powershell
Expand-Archive .\SUMO.zip -DestinationPath .\SUMO_FULL -Force
```

The resulting structure contains:

```text
SUMO_FULL/
└── SUMO/
    ├── Highway/
    │   ├── Low/
    │   ├── Med/
    │   └── High/
    └── Urban/
        ├── Low/
        ├── Med/
        └── High/
```

The detailed paper figures use the Low and High mobility cases.

---

## Paper scenario mapping

```text
Highway Low
SUMO/Highway/Low/
archived trace: simulation_data_highwayl.csv

Highway High
SUMO/Highway/High/
archived trace: simulation_data_highwayh.csv

Urban Low
SUMO/Urban/Low/
archived trace: simulation_data_cityl.csv

Urban High
SUMO/Urban/High/
archived trace: simulation_data_cityh.csv
```

The Medium scenarios are retained in the complete archive for provenance but are not required for the final Low/High detailed-PHY figures.

---

## Inspect a SUMO scenario

Each scenario contains an `osm.sumocfg`.

For example:

```powershell
cd .\SUMO_FULL\SUMO\Highway\Low
sumo-gui -c osm.sumocfg
```

The included `run.bat` performs the same operation:

```text
sumo-gui -c osm.sumocfg
```

---

## Regenerate mobility with TraCI

The supplied `gen_traces.ipynb` notebooks start SUMO through TraCI using:

```python
traci.start(["sumo", "-c", "osm.sumocfg"], port=8873)
```

and record:

```text
SimulationTime
Step
VehicleID
PositionX
PositionY
Speed
Heading
```

Run the notebook from inside the corresponding scenario directory so that `osm.sumocfg` and its referenced network/route files are resolved correctly.

For Highway scenarios, the notebook generates:

```text
simulation_data.csv
```

Rename the generated output according to the scenario:

```powershell
# Highway Low
Rename-Item .\simulation_data.csv simulation_data_highwayl.csv

# Highway High
Rename-Item .\simulation_data.csv simulation_data_highwayh.csv
```

For the Urban `SUMO2CVS.ipynb` workflow, the notebook generates:

```text
simulation_data_city.csv
```

Rename it according to the scenario:

```powershell
# Urban Low
Rename-Item .\simulation_data_city.csv simulation_data_cityl.csv

# Urban High
Rename-Item .\simulation_data_city.csv simulation_data_cityh.csv
```

Do not run two TraCI exporters simultaneously on the same fixed port (`8873`) unless the port is changed.

---

## About `build.bat`

Each scenario also contains `build.bat`.

These scripts invoke SUMO `randomTrips.py` to create traffic demand. They are provided for provenance and for researchers who intentionally want to regenerate the traffic demand.

For reproducing the supplied paper mobility scenarios, **do not rebuild the traffic demand first**. Use the route/trip files already supplied in `SUMO.zip`.

Running `build.bat` creates a newly generated traffic-demand realization and therefore should be treated as mobility regeneration rather than exact replay of the archived scenario input.

---

## Communication-simulation stage

After obtaining the desired mobility trace, place or convert it into the trace format expected by the communication simulator.

The repository contains:

```text
simulator/sumo_export_100ms.py
```

and the communication runners consume mobility using the trace interface.

The paper cases are run independently for SORA and NR-V2X.

Example:

```powershell
python experiments/run_case.py --protocol sora --scenario highway --density low
python experiments/run_case.py --protocol nr   --scenario highway --density low
```

Repeat for:

```text
highway / low
highway / high
urban   / low
urban   / high
```

---

## Aggregate PRR and PIR

After a communication run:

```powershell
python analysis/aggregate_case.py `
  --input results/raw/highway/low/sora/seed_12/receptions.csv `
  --output results/metrics/highway/low/sora/seed_12/prr_pir.csv
```

Repeat for each required scenario/protocol combination.

---

## Generate the manuscript figures

After all required metric files are available:

```powershell
python analysis/plot_paper_figures.py
```

The plotting stage generates the detailed-PHY Highway and Urban PRR/PIR figures.

---

## Reproduction workflow summary

```text
Option 1
Supplied CSV trace
      |
      v
SORA / NR-V2X simulator
      |
      v
PRR / PIR
      |
      v
Figures

Option 2
SUMO.zip
      |
      v
SUMO scenario
      |
      v
TraCI mobility export
      |
      v
Trace preparation
      |
      v
SORA / NR-V2X simulator
      |
      v
PRR / PIR
      |
      v
Figures
```

Option 1 is provided for direct testing and trace-based communication simulation.

Option 2 reproduces the complete SUMO mobility-generation and communication-simulation workflow associated with the paper.

---

## Archive integrity

The complete archive is distributed separately as a GitHub Release asset rather than as a normal Git-tracked file.

Expected file:

```text
SUMO.zip
```

Expected SHA-256:

```text
FFDF39901B079A9492EBC2BECD750F3DCAA51AE5627A847487B3033FC5A02F8F
```
