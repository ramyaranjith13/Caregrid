"""Dataset ingestion pipeline for CareGrid V2.

Column-agnostic: schemas are discovered from the ACTUAL CSV files at import
time. No column names are assumed from the dataset URLs or from memory.

Real Kaggle datasets require manual download (Kaggle blocks anonymous
download with HTTP 403). Drop the CSVs into data/raw/ and import them.
Synthetic demo files (prefixed SYNTHETIC_) are clearly labelled as such.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..database import get_connection

BASE_DIR = Path(__file__).resolve().parent.parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
PROCESSED_DIR = BASE_DIR / "data" / "processed"

DATASETS = {
    # Real operational datasets (uploaded by the user). Kept fully separate
    # from the LIVE CareGrid patient table.
    "ops_patients": {
        "table": "ops_patients",
        "title": "Hospital Operations — Patient Records",
        "filename": "patients.csv",
        "category": "operational",
        "source": "Uploaded operational dataset (patients.csv)",
    },
    "services_weekly": {
        "table": "services_weekly_capacity",
        "title": "Weekly Service Capacity & Demand",
        "filename": "services_weekly.csv",
        "category": "operational",
        "source": "Uploaded operational dataset (services_weekly.csv)",
    },
    "staff": {
        "table": "staff_directory",
        "title": "Staff Directory",
        "filename": "staff.csv",
        "category": "operational",
        "source": "Uploaded operational dataset (staff.csv)",
    },
    "staff_schedule": {
        "table": "staff_schedule",
        "title": "Staff Weekly Schedule",
        "filename": "staff_schedule.csv",
        "category": "operational",
        "source": "Uploaded operational dataset (staff_schedule.csv)",
    },
    # Synthetic dataset retained ONLY to back the clearly-labelled prototype
    # survival model when no real ICU-outcome dataset is available.
    "icu_outcome": {
        "table": "icu_outcome_dataset",
        "title": "ICU Outcome — SYNTHETIC (fallback)",
        "filename": "SYNTHETIC_icu_outcome.csv",
        "category": "ml_synthetic",
        "source": "SYNTHETIC DEMO DATA (not real patients)",
    },
    # Real ICU Patient Outcome Prediction dataset (uploaded X_train + y_train,
    # merged, target = In-hospital_death). Kept separate from live patients.
    "icu_outcome_real": {
        "table": "icu_outcome_real",
        "title": "ICU Patient Outcome Prediction (REAL)",
        "filename": "icu_outcome_real.csv",
        "category": "ml_real",
        "source": "Kaggle: fdemoribajolin/deathclassification-icu (uploaded X_train_2025 + y_train_2025)",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sanitize(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]", "_", str(name).strip())
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "col"
    if s[0].isdigit():
        s = "c_" + s
    return s.lower()


def _unique_columns(cols):
    seen = {}
    out = []
    for c in cols:
        base = _sanitize(c)
        candidate = base
        i = 1
        while candidate in seen:
            i += 1
            candidate = f"{base}_{i}"
        seen[candidate] = True
        out.append(candidate)
    return out


def _infer_sql_type(series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "INTEGER"
    if pd.api.types.is_integer_dtype(series):
        return "INTEGER"
    if pd.api.types.is_float_dtype(series):
        return "REAL"
    if pd.api.types.is_numeric_dtype(series):
        return "REAL"
    return "TEXT"


def inspect_csv(path) -> dict:
    """Inspect a CSV without importing: columns, dtypes, missing, duplicates."""
    df = pd.read_csv(path)
    columns = []
    for c in df.columns:
        s = df[c]
        nunique = int(s.nunique(dropna=True))
        columns.append(
            {
                "name": str(c),
                "dtype": str(s.dtype),
                "sql_type": _infer_sql_type(s),
                "missing": int(s.isna().sum()),
                "unique": nunique,
                "kind": "numeric" if pd.api.types.is_numeric_dtype(s) else "categorical",
                "binary": nunique == 2,
            }
        )
    return {
        "path": str(path),
        "n_rows": int(len(df)),
        "n_cols": int(len(df.columns)),
        "duplicates": int(df.duplicated().sum()),
        "columns": columns,
        "candidate_targets": [c["name"] for c in columns if c["binary"]],
    }


def _file_hash(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def find_raw_file(dataset_key: str):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    fname = DATASETS[dataset_key].get("filename")
    if fname:
        p = RAW_DIR / fname
        if p.exists():
            return p
    return None


def _source_label(dataset_key: str, filename: str) -> str:
    if filename.upper().startswith("SYNTHETIC"):
        return "SYNTHETIC DEMO DATA (not real patients)"
    return DATASETS[dataset_key].get("source", filename)


def import_dataset(dataset_key: str, path=None, dataset_version: str = "v1") -> dict:
    """Idempotent import. Creates the dataset table from the actual columns."""
    if dataset_key not in DATASETS:
        return {"status": "error", "message": f"Unknown dataset '{dataset_key}'"}

    cfg = DATASETS[dataset_key]
    table = cfg["table"]

    if path is None:
        path = find_raw_file(dataset_key)
    if path is None or not Path(path).exists():
        return {
            "status": "missing",
            "message": (
                f"No CSV found in data/raw for '{dataset_key}' "
                f"(expected {cfg.get('filename')}). Upload the file to data/raw/."
            ),
        }

    path = Path(path)
    fhash = _file_hash(path)
    conn = get_connection()

    existing = conn.execute(
        "SELECT id FROM dataset_imports WHERE file_hash = ?", (fhash,)
    ).fetchone()
    if existing is not None:
        conn.close()
        return {
            "status": "skipped",
            "message": "Identical file already imported (idempotent no-op).",
            "file_hash": fhash,
        }

    info = inspect_csv(path)
    df = pd.read_csv(path)
    total = len(df)
    df = df.drop_duplicates().dropna(how="all")
    imported = len(df)
    rejected = total - imported

    sql_cols = _unique_columns(list(df.columns))
    col_types = [_infer_sql_type(df[c]) for c in df.columns]

    conn.execute(f"DROP TABLE IF EXISTS {table}")
    coldefs = ", ".join(f'"{sc}" {ct}' for sc, ct in zip(sql_cols, col_types))
    conn.execute(
        f"CREATE TABLE {table} (row_id INTEGER PRIMARY KEY AUTOINCREMENT, {coldefs})"
    )

    clean = df.astype(object).where(pd.notna(df), None)
    rows = [tuple(r) for r in clean.to_numpy().tolist()]
    placeholders = ",".join(["?"] * len(sql_cols))
    col_list = ",".join(f'"{c}"' for c in sql_cols)
    conn.executemany(
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})", rows
    )

    source = _source_label(dataset_key, path.name)
    conn.execute(
        """
        INSERT INTO dataset_imports(
            dataset_key, table_name, source, file_hash, dataset_version,
            imported_at, total_rows, imported_rows, rejected_rows, columns, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dataset_key,
            table,
            source,
            fhash,
            dataset_version,
            _now(),
            total,
            imported,
            rejected,
            json.dumps(info["columns"]),
            f"src_file={path.name}",
        ),
    )
    conn.commit()
    conn.close()

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(PROCESSED_DIR / f"{table}.csv", index=False)

    return {
        "status": "ok",
        "dataset_key": dataset_key,
        "table": table,
        "source": source,
        "total_rows": total,
        "imported_rows": imported,
        "rejected_rows": rejected,
        "sql_columns": sql_cols,
        "inspection": info,
    }


def dataset_status() -> dict:
    conn = get_connection()
    out = {}
    for key, cfg in DATASETS.items():
        table = cfg["table"]
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        ).fetchone()
        rows = 0
        meta = None
        if exists:
            rows = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            meta = conn.execute(
                "SELECT * FROM dataset_imports WHERE dataset_key = ? ORDER BY id DESC LIMIT 1",
                (key,),
            ).fetchone()
        out[key] = {
            "title": cfg["title"],
            "category": cfg.get("category", "operational"),
            "table": table,
            "loaded": bool(exists and rows > 0),
            "rows": rows,
            "source": (meta["source"] if meta else cfg.get("source")),
            "version": meta["dataset_version"] if meta else None,
            "imported_at": meta["imported_at"] if meta else None,
            "rejected_rows": meta["rejected_rows"] if meta else None,
            "filename": cfg.get("filename"),
        }
    conn.close()
    return out


def get_schema(dataset_key: str):
    conn = get_connection()
    meta = conn.execute(
        "SELECT columns FROM dataset_imports WHERE dataset_key = ? ORDER BY id DESC LIMIT 1",
        (dataset_key,),
    ).fetchone()
    conn.close()
    return json.loads(meta["columns"]) if meta else []


def load_dataframe(dataset_key: str) -> pd.DataFrame:
    cfg = DATASETS[dataset_key]
    conn = get_connection()
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (cfg["table"],),
    ).fetchone()
    if not exists:
        conn.close()
        return pd.DataFrame()
    df = pd.read_sql_query(f"SELECT * FROM {cfg['table']}", conn)
    conn.close()
    if "row_id" in df.columns:
        df = df.drop(columns=["row_id"])
    return df
