# Experiment execution — Decentralized Collision Mitigation for Reliable Broadcast Communication in Safety-Critical Vehicular Networks

`run_case.py` launches exactly one protocol/scenario/density/seed combination for the detailed-PHY communication evaluation.

The package has two higher-level workflows:

- **Option 1:** use the supplied CSV mobility traces to test/run the communication simulator without SUMO;
- **Option 2:** regenerate mobility from the complete `SUMO.zip` release asset, then feed the generated traces into the same communication-simulation path.

Use `--dry-run` to inspect the exact command without creating a results directory.

Examples:

```powershell
python experiments/run_case.py --protocol sora --scenario highway --density high --dry-run
python experiments/run_case.py --protocol nr --scenario urban --density low --dry-run
```

When executed normally, each case writes `receptions.csv`, `run.log` and `command.txt` below `results/raw/...`.

The frozen values in `configs/paper_full_strict_mcs11.json` must remain consistent with the final manuscript; see `AUDIT_STATUS.md`.
