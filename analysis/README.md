# Analysis

The analysis stage is intentionally separated from simulation.

1. `aggregate_case.py` converts one raw reception CSV into distance-binned PRR/PIR sufficient statistics and metrics.
2. `plot_paper_figures.py` scans the aggregated metric tree, pools available seeds by counts/sums, and makes the final highway/urban PRR/PIR figures.

The final plotting step is intentionally easy:

```powershell
python analysis/plot_paper_figures.py
```

No final result CSVs are distributed with the repository.
