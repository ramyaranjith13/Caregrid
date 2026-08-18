"""Survival-likelihood model for CareGrid V2.

Trained ONLY on the imported ICU outcome dataset. Provides a SUPPORTING
estimate (Estimated survival likelihood: XX%). It never assigns beds,
discharges, or makes step-down decisions — the CareGrid prioritization
engine and the doctor remain in control.

Validation metrics are computed from a held-out test split of the actual
imported dataset. No metrics are fabricated. If the dataset is not loaded,
training returns an error asking for the CSV.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import joblib
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
import pandas as pd

from ..database import get_connection
from .ingestion import PROCESSED_DIR, load_dataframe

MODEL_PATH = PROCESSED_DIR / "survival_model.joblib"
MODEL_NAME = "LogisticRegression (prototype survival estimator)"
TARGET_HINTS = [
    "death", "died", "die", "mortalit", "expired", "expire",
    "hospital_death", "in_hospital_death", "outcome", "survive", "survival", "alive",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _binary_columns(df: pd.DataFrame):
    return [c for c in df.columns if df[c].dropna().nunique() == 2]


def _detect_target(df: pd.DataFrame):
    binaries = _binary_columns(df)
    for c in binaries:
        if any(h in c.lower() for h in TARGET_HINTS):
            return c
    return binaries[0] if len(binaries) == 1 else None


def _is_death_target(name: str) -> bool:
    return any(h in name.lower() for h in ["death", "died", "die", "mortalit", "expired"])


def train_survival_model(target_column=None, features=None, test_size: float = 0.25) -> dict:
    df = load_dataframe("icu_outcome")
    if df.empty:
        return {
            "status": "error",
            "message": (
                "ICU outcome dataset is not loaded. Import it first "
                "(manual CSV upload from Kaggle may be required)."
            ),
        }

    target = target_column or _detect_target(df)
    if target is None or target not in df.columns:
        return {
            "status": "error",
            "message": "Could not auto-detect a binary target column. Specify target_column.",
            "candidate_targets": _binary_columns(df),
        }

    y_raw = df[target].dropna()
    uniques = sorted(pd.unique(y_raw).tolist(), key=lambda v: str(v))
    if set(pd.unique(y_raw)).issubset({0, 1, 0.0, 1.0}):
        label_map = {0: 0, 1: 0, 0.0: 0, 1.0: 1}
        y_full = df[target].map(lambda v: int(v) if pd.notna(v) else np.nan)
        positive_label = 1
    else:
        mapping = {uniques[0]: 0, uniques[1]: 1}
        y_full = df[target].map(mapping)
        positive_label = uniques[1]

    num_cols = [
        c
        for c in df.columns
        if c != target
        and pd.api.types.is_numeric_dtype(df[c])
        and not c.lower().endswith("id")
        and c.lower() != "row_id"
    ]
    if features:
        num_cols = [c for c in features if c in num_cols]
    if not num_cols:
        return {"status": "error", "message": "No usable numeric feature columns found."}

    data = df[num_cols].copy()
    data["__target__"] = y_full
    data = data.dropna(subset=["__target__"])
    X = data[num_cols]
    y = data["__target__"].astype(int)

    if y.nunique() < 2:
        return {"status": "error", "message": "Target has fewer than 2 classes after cleaning."}

    stratify = y if y.value_counts().min() >= 2 else None
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=test_size, random_state=42, stratify=stratify
    )

    pipe = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000)),
        ]
    )
    pipe.fit(X_tr, y_tr)

    proba = pipe.predict_proba(X_te)[:, 1]
    pred = (proba >= 0.5).astype(int)

    cm = confusion_matrix(y_te, pred, labels=[0, 1])
    tn, fp, fn, tp = int(cm[0, 0]), int(cm[0, 1]), int(cm[1, 0]), int(cm[1, 1])
    specificity = tn / (tn + fp) if (tn + fp) else 0.0
    try:
        auc = float(roc_auc_score(y_te, proba))
    except ValueError:
        auc = None
    fpr, tpr, _ = roc_curve(y_te, proba)

    coef = pipe.named_steps["clf"].coef_[0]
    importance = sorted(
        [{"feature": f, "weight": float(w)} for f, w in zip(num_cols, coef)],
        key=lambda d: abs(d["weight"]),
        reverse=True,
    )

    death_like = _is_death_target(target)
    metrics = {
        "accuracy": round(float(accuracy_score(y_te, pred)), 4),
        "precision": round(float(precision_score(y_te, pred, zero_division=0)), 4),
        "recall_sensitivity": round(float(recall_score(y_te, pred, zero_division=0)), 4),
        "specificity": round(float(specificity), 4),
        "roc_auc": round(auc, 4) if auc is not None else None,
        "confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
        "roc_curve": {"fpr": [round(x, 4) for x in fpr.tolist()],
                      "tpr": [round(x, 4) for x in tpr.tolist()]},
        "feature_importance": importance,
    }

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "pipeline": pipe,
            "features": num_cols,
            "target": target,
            "death_like": death_like,
            "positive_label": str(positive_label),
        },
        MODEL_PATH,
    )

    conn = get_connection()
    n_prev = conn.execute("SELECT COUNT(*) AS n FROM model_metadata").fetchone()["n"]
    version = f"v{n_prev + 1}"

    src = conn.execute(
        "SELECT source, dataset_version FROM dataset_imports WHERE dataset_key='icu_outcome' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    dataset_source = src["source"] if src else "unknown"
    dataset_version = src["dataset_version"] if src else "unknown"

    conn.execute(
        """
        INSERT INTO model_metadata(
            model_name, model_version, trained_at, dataset_source, dataset_version,
            n_records, n_train, n_test, target_column, target_meaning, features, metrics
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            MODEL_NAME,
            version,
            _now(),
            dataset_source,
            dataset_version,
            int(len(data)),
            int(len(X_tr)),
            int(len(X_te)),
            target,
            "P(survival) = 1 - P(target=1)" if death_like else "P(survival) = P(target=1)",
            json.dumps(num_cols),
            json.dumps(metrics),
        ),
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "model_name": MODEL_NAME,
        "model_version": version,
        "dataset_source": dataset_source,
        "n_records": int(len(data)),
        "n_train": int(len(X_tr)),
        "n_test": int(len(X_te)),
        "target_column": target,
        "features": num_cols,
        "metrics": metrics,
    }


def model_status() -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM model_metadata ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        return {"trained": False, "model_version": None}
    return {
        "trained": True,
        "model_name": row["model_name"],
        "model_version": row["model_version"],
        "trained_at": row["trained_at"],
        "target_column": row["target_column"],
    }


def get_validation() -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM model_metadata ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        return {"trained": False, "message": "Model not trained yet."}
    d = dict(row)
    d["features"] = json.loads(d["features"]) if d["features"] else []
    d["metrics"] = json.loads(d["metrics"]) if d["metrics"] else {}
    d["trained"] = True
    return d


def estimate_survival(feature_values: dict) -> dict:
    if not MODEL_PATH.exists():
        return {"status": "error", "message": "Model not trained yet."}
    bundle = joblib.load(MODEL_PATH)
    features = bundle["features"]
    row = {f: feature_values.get(f, np.nan) for f in features}
    X = pd.DataFrame([row])[features]
    p_positive = float(bundle["pipeline"].predict_proba(X)[:, 1][0])
    survival = (1 - p_positive) if bundle["death_like"] else p_positive
    return {
        "status": "ok",
        "estimated_survival_likelihood": round(survival * 100, 1),
        "note": "ML estimate / prototype output — supporting value only, not a clinical decision.",
        "model_version": model_status().get("model_version"),
    }
