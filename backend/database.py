from pathlib import Path
import sqlite3

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "caregrid.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _add_column_if_missing(cur, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('Doctor','Nurse','Coordinator','Administrator'))
        );

        -- Authenticated application users (email/password + bcrypt hash).
        CREATE TABLE IF NOT EXISTS users_auth (
            user_id TEXT PRIMARY KEY,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('Doctor','Nurse','Coordinator','Administrator')),
            department TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            last_login TEXT
        );

        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            attempt_time TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS patients (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            diagnosis TEXT NOT NULL,
            waiting_minutes INTEGER NOT NULL DEFAULT 0,
            critical INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'waiting'
        );

        CREATE TABLE IF NOT EXISTS icu_beds (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            patient_id TEXT,
            patient_name TEXT,
            diagnosis TEXT,
            equipment TEXT,
            isolation TEXT,
            compatibility TEXT
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time TEXT NOT NULL,
            patient_id TEXT,
            diagnosis TEXT,
            recommendation TEXT,
            decision TEXT,
            decision_maker TEXT,
            reason TEXT
        );

        -- Step-down bed inventory (logical separation from ICU beds).
        CREATE TABLE IF NOT EXISTS step_down_beds (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            patient_id TEXT,
            patient_name TEXT,
            diagnosis TEXT,
            equipment TEXT,
            isolation TEXT,
            compatibility TEXT
        );

        -- Imported Kaggle/synthetic dataset registry (metadata only; the raw
        -- rows live in per-dataset tables created at import time).
        CREATE TABLE IF NOT EXISTS dataset_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_key TEXT NOT NULL,
            table_name TEXT NOT NULL,
            source TEXT,
            file_hash TEXT UNIQUE,
            dataset_version TEXT,
            imported_at TEXT,
            total_rows INTEGER,
            imported_rows INTEGER,
            rejected_rows INTEGER,
            columns TEXT,
            notes TEXT
        );

        -- Survival model training/validation registry.
        CREATE TABLE IF NOT EXISTS model_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name TEXT,
            model_version TEXT,
            trained_at TEXT,
            dataset_source TEXT,
            dataset_version TEXT,
            n_records INTEGER,
            n_train INTEGER,
            n_test INTEGER,
            target_column TEXT,
            target_meaning TEXT,
            features TEXT,
            metrics TEXT
        );
        """
    )

    # Phase 2 additions to the patient model.
    for column, definition in [
        ("severity", "REAL NOT NULL DEFAULT 0"),
        ("dependency", "REAL NOT NULL DEFAULT 0"),
        ("deterioration", "REAL NOT NULL DEFAULT 0"),
        ("survival_likelihood", "REAL NOT NULL DEFAULT 0"),
        ("resource", "REAL NOT NULL DEFAULT 0"),
        ("required_resources", "TEXT NOT NULL DEFAULT ''"),
    ]:
        _add_column_if_missing(cur, "patients", column, definition)

    # Phase 1 (V2) additions: patient age.
    _add_column_if_missing(cur, "patients", "age", "INTEGER NOT NULL DEFAULT 0")

    # Phase 1 (V2) additions: rich audit trail columns.
    for column, definition in [
        ("patient_name", "TEXT"),
        ("event", "TEXT"),
        ("previous_value", "TEXT"),
        ("new_value", "TEXT"),
        ("actor_email", "TEXT"),
        ("actor_role", "TEXT"),
    ]:
        _add_column_if_missing(cur, "audit_logs", column, definition)

    existing_users = cur.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if existing_users == 0:
        cur.executemany(
            "INSERT INTO users(name, role) VALUES (?, ?)",
            [
                ("Dr. S. Krishnan", "Doctor"),
                ("Nurse on Duty", "Nurse"),
                ("ICU Coordinator", "Coordinator"),
                ("Hospital Administrator", "Administrator"),
            ],
        )

    # Fictional hackathon patients and prototype scoring factors.
    # Tuple order: id,name,age,diagnosis,waiting_minutes,critical,status,
    #              severity,dependency,deterioration,survival_likelihood,resource,required_resources
    demo_patients = [
        ("P003", "R. Kalyan", 61, "Septic shock", 55, 1, "waiting", 31, 24, 18, 90, 7, "Ventilator, Cardiac Monitor, Vasopressor support"),
        ("P001", "A. Fernandes", 58, "Cardiac arrest, post-ROSC", 35, 1, "waiting", 32, 24, 17, 91, 5, "Ventilator, Cardiac Monitor"),
        ("P005", "S. Iyer", 72, "Acute ischemic stroke", 125, 1, "waiting", 27, 21, 16, 86, 4, "Neuro monitoring, Oxygen"),
        ("P002", "M. Devaraj", 49, "Acute respiratory failure", 250, 0, "waiting", 24, 18, 12, 84, 8, "Ventilator, Oxygen"),
        ("P004", "K. Ramesh", 66, "Post-operative monitoring", 380, 0, "waiting", 16, 12, 9, 82, 9, "Cardiac Monitor, Infusion Pump"),
    ]

    for row in demo_patients:
        cur.execute(
            """
            INSERT INTO patients(
                id,name,age,diagnosis,waiting_minutes,critical,status,
                severity,dependency,deterioration,survival_likelihood,resource,required_resources
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                age=excluded.age,
                diagnosis=excluded.diagnosis,
                waiting_minutes=excluded.waiting_minutes,
                critical=excluded.critical,
                status=excluded.status,
                severity=excluded.severity,
                dependency=excluded.dependency,
                deterioration=excluded.deterioration,
                survival_likelihood=excluded.survival_likelihood,
                resource=excluded.resource,
                required_resources=excluded.required_resources
            """,
            row,
        )

    existing_beds = cur.execute("SELECT COUNT(*) AS n FROM icu_beds").fetchone()["n"]
    if existing_beds == 0:
        beds = [
            ("ICU-01", "occupied", "P007", "N. Suresh", "Multi-organ dysfunction", "Ventilator,Cardiac Monitor,Dialysis", "Active", "High"),
            ("ICU-02", "occupied", "P009", "V. Lakshmi", "COPD exacerbation", "Ventilator,Oxygen", "Not required", "High"),
            ("ICU-03", "occupied", "P008", "J. Prakash", "Post-operative complication", "Ventilator,Monitoring", "Not required", "Medium"),
            ("ICU-04", "occupied", "P010", "H. Anand", "Diabetic ketoacidosis", "Cardiac Monitor,Infusion Pump", "Not required", "High"),
            ("ICU-05", "occupied", "P011", "D. Chandran", "Traumatic brain injury", "Ventilator,ICP Monitor", "Not required", "High"),
            ("ICU-06", "cleaning", None, None, None, "Ventilator,Cardiac Monitor", "Pending turnover", "—"),
            ("ICU-07", "available", None, None, None, "Ventilator,Cardiac Monitor,Oxygen", "Available", "High"),
            ("ICU-08", "occupied", "P012", "R. Meena", "Severe pneumonia", "Ventilator,Oxygen", "Airborne precautions", "Medium"),
        ]
        cur.executemany(
            """
            INSERT INTO icu_beds(
                id,status,patient_id,patient_name,diagnosis,equipment,isolation,compatibility
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            beds,
        )

    existing_sd = cur.execute("SELECT COUNT(*) AS n FROM step_down_beds").fetchone()["n"]
    if existing_sd == 0:
        step_down = [
            ("SD-01", "available", None, None, None, "Cardiac Monitor,Oxygen", "Not required", "High"),
            ("SD-02", "occupied", "P020", "L. Menon", "Recovering pneumonia", "Oxygen", "Not required", "Medium"),
            ("SD-03", "available", None, None, None, "Telemetry,Oxygen", "Not required", "High"),
            ("SD-04", "occupied", "P021", "G. Rao", "Post-ICU step-down", "Telemetry", "Not required", "Medium"),
            ("SD-05", "cleaning", None, None, None, "Cardiac Monitor", "Pending turnover", "—"),
        ]
        cur.executemany(
            """
            INSERT INTO step_down_beds(
                id,status,patient_id,patient_name,diagnosis,equipment,isolation,compatibility
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            step_down,
        )

    conn.commit()
    conn.close()
