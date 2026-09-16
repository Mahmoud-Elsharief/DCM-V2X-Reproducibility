# SUMO workflow for **Decentralized Collision Mitigation for Reliable Broadcast Communication in Safety-Critical Vehicular Networks**

The reproducibility package supports two intentionally different mobility workflows.

## Option 1 — Test the communication simulator using the supplied CSV traces

This option does **not** require SUMO.

Use the CSV mobility traces under:

```text
../mobility/frozen/
```

The files can also be opened in Excel for inspection. They are read directly by the SORA/NR-V2X simulator and are intended for implementation testing, short development runs and direct trace-based communication experiments.

## Option 2 — Reproduce the complete paper workflow from `SUMO.zip`

The complete SUMO mobility-generation package is distributed as a separate GitHub Release asset:

```text
SUMO.zip
```

This option **does require SUMO**.

The workflow is:

```text
download SUMO.zip
    ↓
verify SHA-256
    ↓
extract the archive
    ↓
run the paper SUMO scenarios
    ↓
export/generate mobility
    ↓
convert to the simulator CSV schema at 100-ms resolution
    ↓
run SORA and NR-V2X
    ↓
aggregate PRR/PIR
    ↓
generate the detailed manuscript figures
```

### Why the complete archive is separate

The full SUMO archive is substantially larger than the source repository, so it is intended to be attached to the corresponding GitHub Release rather than tracked as a normal repository file.

### Final archive-specific instructions

The exact commands will be completed after the final release `SUMO.zip` is inspected. This document will then record:

- the published SHA-256 checksum;
- the expected extracted directory structure;
- the tested SUMO version;
- `SUMO_HOME` configuration;
- exact Highway Low/High and Urban Low/High scenario paths;
- exact SUMO commands;
- source simulation windows;
- trace-conversion commands;
- expected output schema/statistics;
- verification steps before communication simulation.

The selected small SUMO files already present in this directory are retained for provenance. They are **not a substitute for the complete `SUMO.zip` when following Option 2**.

A newly regenerated mobility trace should not be claimed to be byte-identical to a supplied trace unless its checksum actually matches.
