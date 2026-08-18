# CareGrid V2 — Datasets

This folder holds dataset files for the ingestion + ML pipeline.

```
data/
  raw/         # source CSV files (git-ignored)
  processed/   # cleaned CSVs + trained model artifacts (git-ignored)
```

## Kaggle datasets (MANUAL UPLOAD REQUIRED)

Anonymous Kaggle downloads return **HTTP 403** — the files cannot be
auto-downloaded in this environment. To use the real data:

1. **Hospital Beds Management — Synthetic**
   https://www.kaggle.com/datasets/jaderz/hospitalbeds-management
   → download the CSV and place it in `data/raw/`

2. **ICU Patient Outcome Prediction**
   https://www.kaggle.com/datasets/fdemoribajolin/deathclassification-icu
   → download the CSV and place it in `data/raw/`

Then open **Analytics / Validation** in the app (as Doctor) and click
**Import** for each dataset, then **Train survival model**.

The ingestion pipeline **inspects the actual columns** of whatever CSV you
provide — it does not assume column names. Any file whose name contains
`icu` / `death` / `outcome` / `mortality` is treated as the ICU outcome
dataset; any file containing `hospital` / `bed` is treated as the hospital
beds dataset.

## Synthetic demo data

`SYNTHETIC_icu_outcome.csv` and `SYNTHETIC_hospital_beds.csv` are generated
by `scripts/generate_synthetic_datasets.py`. They are **clearly labelled
synthetic** (not the Kaggle data) and exist only so the pipeline is
demonstrable before the real CSVs are uploaded. All validation metrics
shown for synthetic data are real metrics computed from that synthetic set.

No real / private patient data is committed to this repository.
