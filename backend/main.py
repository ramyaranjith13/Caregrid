from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .database import get_connection, init_db
from .services.prioritization import calculate_priority, rank_patients

app = FastAPI(title="CareGrid API", version="0.2.0")


class PatientCreate(BaseModel):
    id: str = Field(min_length=2)
    name: str = Field(min_length=1)
    diagnosis: str = Field(min_length=1)
    waiting_minutes: int = Field(default=0, ge=0)
    critical: bool = False
    severity: float = Field(default=0, ge=0, le=35)
    dependency: float = Field(default=0, ge=0, le=25)
    deterioration: float = Field(default=0, ge=0, le=20)
    survival_likelihood: float = Field(default=0, ge=0, le=100)
    resource: float = Field(default=0, ge=0, le=10)
    required_resources: str = ""


class DecisionCreate(BaseModel):
    patient_id: str
    decision: str
    decision_maker: str
    reason: str = "—"
    recommendation: str


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"status": "ok", "service": "caregrid-api"}


@app.get("/patients")
def patients():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM patients WHERE status = 'waiting'"
    ).fetchall()
    conn.close()
    return rank_patients(rows)


@app.get("/patients/{patient_id}")
def patient_detail(patient_id: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM patients WHERE id = ?", (patient_id,)
    ).fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    ranked = rank_patients([row])
    return ranked[0]


@app.get("/icu/beds")
def beds():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM icu_beds ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/patients")
def create_patient(payload: PatientCreate):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO patients(
                id,name,diagnosis,waiting_minutes,critical,status,
                severity,dependency,deterioration,survival_likelihood,resource,required_resources
            )
            VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.id,
                payload.name,
                payload.diagnosis,
                payload.waiting_minutes,
                int(payload.critical),
                payload.severity,
                payload.dependency,
                payload.deterioration,
                payload.survival_likelihood,
                payload.resource,
                payload.required_resources,
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Unable to create patient: {exc}")
    conn.close()
    return {"message": "patient created", "patient_id": payload.id}


@app.post("/priority/calculate")
def priority(scores: dict):
    return {"score": calculate_priority(scores)}


@app.post("/allocation")
def allocation(payload: DecisionCreate):
    if payload.decision not in {"ACCEPT", "OVERRIDE", "DEFER"}:
        raise HTTPException(
            status_code=400,
            detail="Decision must be ACCEPT, OVERRIDE, or DEFER",
        )

    if payload.decision in {"OVERRIDE", "DEFER"} and not payload.reason.strip():
        raise HTTPException(
            status_code=400,
            detail="Reason is required for Override/Defer",
        )

    conn = get_connection()
    patient = conn.execute(
        "SELECT diagnosis FROM patients WHERE id = ?", (payload.patient_id,)
    ).fetchone()

    if patient is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Patient not found")

    conn.execute(
        """
        INSERT INTO audit_logs(
            event_time,patient_id,diagnosis,recommendation,
            decision,decision_maker,reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            payload.patient_id,
            patient["diagnosis"],
            payload.recommendation,
            payload.decision,
            payload.decision_maker,
            payload.reason,
        ),
    )

    if payload.decision == "DEFER":
        conn.execute(
            "UPDATE patients SET status = 'deferred' WHERE id = ?",
            (payload.patient_id,),
        )

    conn.commit()
    conn.close()

    return {"message": "decision recorded"}


@app.get("/audit-log")
def audit_log():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM audit_logs ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
