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
    demo_patients = [
        ("P003", "R. Kalyan", "Septic shock", 55, 1, "waiting", 31, 24, 18, 90, 7, "Ventilator, Cardiac Monitor, Vasopressor support"),
        ("P001", "A. Fernandes", "Cardiac arrest, post-ROSC", 35, 1, "waiting", 32, 24, 17, 91, 5, "Ventilator, Cardiac Monitor"),
        ("P005", "S. Iyer", "Acute ischemic stroke", 125, 1, "waiting", 27, 21, 16, 86, 4, "Neuro monitoring, Oxygen"),
        ("P002", "M. Devaraj", "Acute respiratory failure", 250, 0, "waiting", 24, 18, 12, 84, 8, "Ventilator, Oxygen"),
        ("P004", "K. Ramesh", "Post-operative monitoring", 380, 0, "waiting", 16, 12, 9, 82, 9, "Cardiac Monitor, Infusion Pump"),
    ]

    for row in demo_patients:
        cur.execute(
            """
            INSERT INTO patients(
                id,name,diagnosis,waiting_minutes,critical,status,
                severity,dependency,deterioration,survival_likelihood,resource,required_resources
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
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

    conn.commit()
    conn.close()
