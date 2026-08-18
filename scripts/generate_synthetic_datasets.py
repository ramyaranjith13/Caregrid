"""Generate clearly-labelled SYNTHETIC demo datasets for CareGrid V2.

These are NOT the Kaggle datasets. They exist so the ingestion + ML +
validation pipeline is demonstrable before the real Kaggle CSVs are
uploaded manually. Filenames are prefixed SYNTHETIC_ and the ingestion
pipeline labels their source accordingly.

Real data: download from Kaggle and place in data/raw/ (any CSV name),
then re-import from the Analytics / Validation page.
"""

from pathlib import Path

import numpy as np
import pandas as pd

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def make_icu_outcome(n=1500, seed=42):
    rng = np.random.default_rng(seed)
    age = rng.integers(18, 92, n)
    heart_rate = rng.normal(92, 18, n).clip(40, 190).round(0)
    resp_rate = rng.normal(20, 6, n).clip(6, 45).round(0)
    spo2 = rng.normal(95, 5, n).clip(60, 100).round(0)
    systolic_bp = rng.normal(118, 22, n).clip(60, 210).round(0)
    lactate = rng.normal(2.2, 1.6, n).clip(0.3, 15).round(2)
    gcs = rng.integers(3, 16, n)
    sofa_score = rng.integers(0, 20, n)
    comorbidity_count = rng.integers(0, 7, n)
    ventilated = rng.integers(0, 2, n)

    # Latent risk drives the (synthetic) mortality target.
    z = (
        0.035 * (age - 60)
        + 0.30 * (sofa_score - 6)
        + 0.45 * (lactate - 2)
        + 0.10 * (resp_rate - 18)
        - 0.12 * (gcs - 10)
        - 0.06 * (spo2 - 95)
        + 0.35 * comorbidity_count
        + 0.5 * ventilated
        - 3.0
    )
    prob = 1 / (1 + np.exp(-z / 3.0))
    hospital_death = (rng.random(n) < prob).astype(int)

    df = pd.DataFrame(
        {
            "patient_ref": [f"S{100000 + i}" for i in range(n)],
            "age": age,
            "heart_rate": heart_rate,
            "resp_rate": resp_rate,
            "spo2": spo2,
            "systolic_bp": systolic_bp,
            "lactate": lactate,
            "gcs": gcs,
            "sofa_score": sofa_score,
            "comorbidity_count": comorbidity_count,
            "ventilated": ventilated,
            "hospital_death": hospital_death,
        }
    )
    # inject a few missing values to exercise cleaning
    for col in ["lactate", "spo2"]:
        idx = rng.choice(n, size=int(n * 0.03), replace=False)
        df.loc[idx, col] = np.nan
    return df


def make_hospital_beds(n=600, seed=7):
    rng = np.random.default_rng(seed)
    wards = ["ICU", "Step-Down", "General", "Cardiac", "Respiratory", "Neuro"]
    regions = ["North", "South", "East", "West", "Central"]
    total = rng.integers(8, 40, n)
    occupied = (total * rng.uniform(0.4, 0.98, n)).astype(int)
    cleaning = rng.integers(0, 4, n)
    available = (total - occupied - cleaning).clip(0, None)
    df = pd.DataFrame(
        {
            "record_date": pd.to_datetime("2025-01-01")
            + pd.to_timedelta(rng.integers(0, 365, n), unit="D"),
            "ward": rng.choice(wards, n),
            "region": rng.choice(regions, n),
            "total_beds": total,
            "occupied_beds": occupied,
            "cleaning_beds": cleaning,
            "available_beds": available,
            "has_ventilator": rng.integers(0, 2, n),
            "has_isolation": rng.integers(0, 2, n),
        }
    )
    return df


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    icu = make_icu_outcome()
    beds = make_hospital_beds()
    icu.to_csv(RAW_DIR / "SYNTHETIC_icu_outcome.csv", index=False)
    beds.to_csv(RAW_DIR / "SYNTHETIC_hospital_beds.csv", index=False)
    print(f"Wrote {len(icu)} ICU outcome rows and {len(beds)} hospital bed rows to {RAW_DIR}")


if __name__ == "__main__":
    main()
