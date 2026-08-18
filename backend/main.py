from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .database import get_connection, init_db
from .services.prioritization import (
    calculate_priority,
    rank_patients,
    score_breakdown_from_patient,
)
from .services import ingestion, ml_model

app = FastAPI(title="CareGrid API", version="0.3.0")


class PatientCreate(BaseModel):
    id: str = Field(min_length=2)
    name: str = Field(min_length=1)
    diagnosis: str = Field(min_length=1)
    age: int = Field(default=0, ge=0, le=130)
    waiting_minutes: int = Field(default=0, ge=0)
    critical: bool = False
    severity: float = Field(default=0, ge=0, le=35)
    dependency: float = Field(default=0, ge=0, le=25)
    deterioration: float = Field(default=0, ge=0, le=20)
    survival_likelihood: float = Field(default=0, ge=0, le=100)
    resource: float = Field(default=0, ge=0, le=10)
    required_resources: str = ""
    decision_maker: str = "Doctor"


class PatientUpdate(BaseModel):
    """Clinical-assessment update. All clinical fields optional (partial update)."""

    name: Optional[str] = None
    diagnosis: Optional[str] = None
    age: Optional[int] = Field(default=None, ge=0, le=130)
    waiting_minutes: Optional[int] = Field(default=None, ge=0)
    critical: Optional[bool] = None
    severity: Optional[float] = Field(default=None, ge=0, le=35)
    dependency: Optional[float] = Field(default=None, ge=0, le=25)
    deterioration: Optional[float] = Field(default=None, ge=0, le=20)
    survival_likelihood: Optional[float] = Field(default=None, ge=0, le=100)
    resource: Optional[float] = Field(default=None, ge=0, le=10)
    required_resources: Optional[str] = None
    decision_maker: str = "Doctor"


class DecisionCreate(BaseModel):
    patient_id: str
    decision: str
    decision_maker: str
    reason: str = "—"
    recommendation: str


DECISION_EVENTS = {
    "ACCEPT": "DECISION_ACCEPTED",
    "OVERRIDE": "DECISION_OVERRIDDEN",
    "DEFER": "DECISION_DEFERRED",
}

# Human-readable labels used in the audit trail for clinical fields.
FIELD_LABELS = {
    "name": "Patient name",
    "diagnosis": "Diagnosis",
    "age": "Age",
    "waiting_minutes": "Waiting time (min)",
    "critical": "Critical status",
    "severity": "Clinical urgency (severity)",
    "dependency": "Critical-care dependency",
    "deterioration": "Deterioration risk",
    "survival_likelihood": "Survival likelihood",
    "resource": "Resource compatibility",
    "required_resources": "Required resources",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_audit(
    conn,
    *,
    event: str,
    event_time: Optional[str] = None,
    patient_id: Optional[str] = None,
    patient_name: Optional[str] = None,
    diagnosis: Optional[str] = None,
    previous_value: Optional[str] = None,
    new_value: Optional[str] = None,
    recommendation: Optional[str] = None,
    decision: Optional[str] = None,
    decision_maker: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Central audit writer. Every mutation should funnel through here."""
    conn.execute(
        """
        INSERT INTO audit_logs(
            event_time, patient_id, patient_name, diagnosis, event,
            previous_value, new_value, recommendation, decision,
            decision_maker, reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_time or _now(),
            patient_id,
            patient_name,
            diagnosis,
            event,
            previous_value,
            new_value,
            recommendation,
            decision,
            decision_maker,
            reason,
        ),
    )


def _waiting_rank_map(conn):
    """Return {patient_id: {rank, priority_score, name, diagnosis}} for the
    current waiting queue, using the same ranking engine as the API."""
    rows = conn.execute("SELECT * FROM patients WHERE status = 'waiting'").fetchall()
    ranked = rank_patients(rows)
    return {
        r["id"]: {
            "rank": r["rank"],
            "priority_score": r["priority_score"],
            "name": r.get("name"),
            "diagnosis": r.get("diagnosis"),
        }
        for r in ranked
    }


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

    existing = conn.execute(
        "SELECT id FROM patients WHERE id = ?", (payload.id,)
    ).fetchone()
    if existing is not None:
        conn.close()
        raise HTTPException(
            status_code=400, detail=f"Patient ID '{payload.id}' already exists"
        )

    try:
        conn.execute(
            """
            INSERT INTO patients(
                id,name,age,diagnosis,waiting_minutes,critical,status,
                severity,dependency,deterioration,survival_likelihood,resource,required_resources
            )
            VALUES (?, ?, ?, ?, ?, ?, 'waiting', ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.id,
                payload.name,
                payload.age,
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
    except Exception as exc:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Unable to create patient: {exc}")

    ts = _now()
    score = calculate_priority(score_breakdown_from_patient(payload.model_dump()))

    log_audit(
        conn,
        event="PATIENT_CREATED",
        event_time=ts,
        patient_id=payload.id,
        patient_name=payload.name,
        diagnosis=payload.diagnosis,
        new_value=f"Registered (age {payload.age}, critical={bool(payload.critical)})",
        decision_maker=payload.decision_maker,
    )
    log_audit(
        conn,
        event="SCORE_CALCULATED",
        event_time=ts,
        patient_id=payload.id,
        patient_name=payload.name,
        diagnosis=payload.diagnosis,
        new_value=f"{score}/100",
        decision_maker=payload.decision_maker,
    )

    conn.commit()

    # Recompute the queue and log the new rank / recommendation.
    ranks = _waiting_rank_map(conn)
    if payload.id in ranks:
        new_rank = ranks[payload.id]["rank"]
        log_audit(
            conn,
            event="RECOMMENDATION_GENERATED",
            event_time=ts,
            patient_id=payload.id,
            patient_name=payload.name,
            diagnosis=payload.diagnosis,
            recommendation=f"Priority #{new_rank}",
            new_value=f"Priority #{new_rank}",
            decision_maker=payload.decision_maker,
        )
        conn.commit()

    conn.close()
    return {"message": "patient created", "patient_id": payload.id, "priority_score": score}


@app.patch("/patients/{patient_id}")
def update_patient(patient_id: str, payload: PatientUpdate):
    """Clinical deterioration / assessment update.

    Applies changes, recalculates the score, re-ranks the waiting queue and
    writes a rich audit trail (ASSESSMENT_UPDATED, SCORE_CALCULATED,
    RANK_CHANGED, RECOMMENDATION_GENERATED). Returns a 'what changed' diff.
    """
    conn = get_connection()
    current = conn.execute(
        "SELECT * FROM patients WHERE id = ?", (patient_id,)
    ).fetchone()

    if current is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Patient not found")

    current = dict(current)

    updates = payload.model_dump(exclude_unset=True, exclude={"decision_maker"})

    changed_factors = []
    apply_values = {}
    for field, new_val in updates.items():
        old_val = current.get(field)
        if field == "critical":
            new_norm = int(bool(new_val))
            old_norm = int(bool(old_val))
        else:
            new_norm = new_val
            old_norm = old_val
        if str(old_norm) != str(new_norm):
            apply_values[field] = new_norm
            changed_factors.append(
                {
                    "field": field,
                    "label": FIELD_LABELS.get(field, field),
                    "old": old_norm,
                    "new": new_norm,
                }
            )

    if not apply_values:
        conn.close()
        return {
            "message": "no changes",
            "patient_id": patient_id,
            "changed_factors": [],
        }

    before = _waiting_rank_map(conn)
    prev_score = before.get(patient_id, {}).get("priority_score")
    prev_rank = before.get(patient_id, {}).get("rank")

    set_clause = ", ".join(f"{field} = ?" for field in apply_values)
    conn.execute(
        f"UPDATE patients SET {set_clause} WHERE id = ?",
        (*apply_values.values(), patient_id),
    )
    conn.commit()

    after = _waiting_rank_map(conn)
    new_score = after.get(patient_id, {}).get("priority_score")
    new_rank = after.get(patient_id, {}).get("rank")

    ts = _now()
    maker = payload.decision_maker
    p_name = apply_values.get("name", current.get("name"))
    p_diag = apply_values.get("diagnosis", current.get("diagnosis"))

    for change in changed_factors:
        log_audit(
            conn,
            event="ASSESSMENT_UPDATED",
            event_time=ts,
            patient_id=patient_id,
            patient_name=p_name,
            diagnosis=p_diag,
            previous_value=f"{change['label']}: {change['old']}",
            new_value=f"{change['label']}: {change['new']}",
            decision_maker=maker,
        )

    if prev_score != new_score:
        log_audit(
            conn,
            event="SCORE_CALCULATED",
            event_time=ts,
            patient_id=patient_id,
            patient_name=p_name,
            diagnosis=p_diag,
            previous_value=f"{prev_score}/100" if prev_score is not None else None,
            new_value=f"{new_score}/100" if new_score is not None else None,
            decision_maker=maker,
        )

    # Log rank changes for every waiting patient whose rank moved.
    for pid, info in after.items():
        old_rank = before.get(pid, {}).get("rank")
        if old_rank is not None and old_rank != info["rank"]:
            log_audit(
                conn,
                event="RANK_CHANGED",
                event_time=ts,
                patient_id=pid,
                patient_name=info.get("name"),
                diagnosis=info.get("diagnosis"),
                previous_value=f"#{old_rank}",
                new_value=f"#{info['rank']}",
                decision_maker=maker,
            )

    if new_rank is not None:
        log_audit(
            conn,
            event="RECOMMENDATION_GENERATED",
            event_time=ts,
            patient_id=patient_id,
            patient_name=p_name,
            diagnosis=p_diag,
            recommendation=f"Priority #{new_rank}",
            previous_value=f"Priority #{prev_rank}" if prev_rank is not None else None,
            new_value=f"Priority #{new_rank}",
            decision_maker=maker,
        )

    conn.commit()
    conn.close()

    return {
        "message": "patient updated",
        "patient_id": patient_id,
        "previous_score": prev_score,
        "new_score": new_score,
        "previous_rank": prev_rank,
        "new_rank": new_rank,
        "changed_factors": changed_factors,
        "updated_by": maker,
        "timestamp": ts,
    }


@app.post("/priority/calculate")
def priority(scores: dict):
    return {"score": calculate_priority(scores)}


@app.post("/allocation")
def allocation(payload: DecisionCreate):
    if payload.decision not in DECISION_EVENTS:
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
        "SELECT name, diagnosis FROM patients WHERE id = ?", (payload.patient_id,)
    ).fetchone()

    if patient is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Patient not found")

    log_audit(
        conn,
        event=DECISION_EVENTS[payload.decision],
        patient_id=payload.patient_id,
        patient_name=patient["name"],
        diagnosis=patient["diagnosis"],
        recommendation=payload.recommendation,
        decision=payload.decision,
        decision_maker=payload.decision_maker,
        reason=payload.reason,
    )

    if payload.decision == "DEFER":
        conn.execute(
            "UPDATE patients SET status = 'deferred' WHERE id = ?",
            (payload.patient_id,),
        )
        # Deferral removes the patient from the waiting queue -> log re-ranks.
        after = _waiting_rank_map(conn)
        ts = _now()
        for pid, info in after.items():
            log_audit(
                conn,
                event="RANK_CHANGED",
                event_time=ts,
                patient_id=pid,
                patient_name=info.get("name"),
                diagnosis=info.get("diagnosis"),
                new_value=f"#{info['rank']}",
                decision_maker=payload.decision_maker,
            )

    conn.commit()
    conn.close()

    return {"message": "decision recorded"}


@app.get("/audit-log")
def audit_log(patient_id: Optional[str] = None):
    conn = get_connection()
    if patient_id:
        rows = conn.execute(
            "SELECT * FROM audit_logs WHERE patient_id = ? ORDER BY id DESC",
            (patient_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_logs ORDER BY id DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/step-down/beds")
def step_down_beds():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM step_down_beds ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------------
# Dataset ingestion + ML validation (CareGrid V2)
# ----------------------------------------------------------------------------


class TrainRequest(BaseModel):
    target_column: Optional[str] = None
    features: Optional[list] = None
    test_size: float = Field(default=0.25, gt=0.05, lt=0.6)
    trained_by: str = "Doctor"


class EstimateRequest(BaseModel):
    features: dict


@app.get("/datasets/status")
def datasets_status():
    return ingestion.dataset_status()


@app.get("/datasets/{dataset_key}/inspect")
def datasets_inspect(dataset_key: str):
    if dataset_key not in ingestion.DATASETS:
        raise HTTPException(status_code=404, detail="Unknown dataset")
    path = ingestion.find_raw_file(dataset_key)
    if path is None:
        return {
            "status": "missing",
            "message": (
                f"No CSV found in data/raw for '{dataset_key}'. "
                f"Manual upload required from {ingestion.DATASETS[dataset_key]['url']}."
            ),
        }
    return {"status": "ok", "inspection": ingestion.inspect_csv(path)}


@app.get("/datasets/{dataset_key}/schema")
def datasets_schema(dataset_key: str):
    if dataset_key not in ingestion.DATASETS:
        raise HTTPException(status_code=404, detail="Unknown dataset")
    return ingestion.get_schema(dataset_key)


@app.post("/datasets/{dataset_key}/import")
def datasets_import(dataset_key: str):
    if dataset_key not in ingestion.DATASETS:
        raise HTTPException(status_code=404, detail="Unknown dataset")
    result = ingestion.import_dataset(dataset_key)
    if result.get("status") == "ok":
        conn = get_connection()
        log_audit(
            conn,
            event="DATASET_IMPORTED",
            new_value=(
                f"{dataset_key}: {result['imported_rows']} rows "
                f"({result['rejected_rows']} rejected)"
            ),
            reason=result.get("source"),
        )
        conn.commit()
        conn.close()
    return result


@app.get("/ml/status")
def ml_status():
    return ml_model.model_status()


@app.get("/ml/validation")
def ml_validation():
    return ml_model.get_validation()


@app.post("/ml/train")
def ml_train(payload: TrainRequest):
    result = ml_model.train_survival_model(
        target_column=payload.target_column,
        features=payload.features,
        test_size=payload.test_size,
    )
    if result.get("status") == "ok":
        conn = get_connection()
        log_audit(
            conn,
            event="MODEL_TRAINED",
            new_value=(
                f"{result['model_version']} · target={result['target_column']} · "
                f"AUC={result['metrics'].get('roc_auc')}"
            ),
            decision_maker=payload.trained_by,
            reason=result.get("dataset_source"),
        )
        conn.commit()
        conn.close()
    return result


@app.post("/ml/estimate")
def ml_estimate(payload: EstimateRequest):
    return ml_model.estimate_survival(payload.features)
