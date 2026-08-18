import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field

from .database import get_connection, init_db
from .services.prioritization import (
    calculate_priority,
    rank_patients,
    score_breakdown_from_patient,
    care_level_recommendation,
)
from .services import ingestion, ml_model, bed_matching
from . import auth

app = FastAPI(title="CareGrid API", version="0.4.0")


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
    actor_email: Optional[str] = None,
    actor_role: Optional[str] = None,
) -> None:
    """Central audit writer. Every mutation should funnel through here."""
    conn.execute(
        """
        INSERT INTO audit_logs(
            event_time, patient_id, patient_name, diagnosis, event,
            previous_value, new_value, recommendation, decision,
            decision_maker, reason, actor_email, actor_role
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            actor_email,
            actor_role,
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
    auth.seed_users()


# ----------------------------------------------------------------------------
# Authentication
# ----------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class UserCreate(BaseModel):
    full_name: str
    email: str
    password: str
    role: str
    department: str = ""
    active: bool = True


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = None
    active: Optional[bool] = None


class PasswordReset(BaseModel):
    new_password: str


@app.post("/auth/login")
def auth_login(payload: LoginRequest):
    return auth.authenticate(payload.email, payload.password)


@app.get("/auth/me")
def auth_me(user: dict = Depends(auth.get_current_user)):
    return user


@app.post("/auth/logout")
def auth_logout(user: dict = Depends(auth.get_current_user)):
    # Stateless JWT: the client discards the token. Endpoint confirms identity.
    return {"message": "logged out"}


@app.get("/users")
def users_list(admin: dict = Depends(auth.require_role("Administrator"))):
    return auth.list_users()


@app.post("/users")
def users_create(payload: UserCreate, admin: dict = Depends(auth.require_role("Administrator"))):
    created = auth.create_user(
        payload.full_name, payload.email, payload.password,
        payload.role, payload.department, payload.active,
    )
    conn = get_connection()
    log_audit(
        conn, event="USER_CREATED", patient_id=None,
        new_value=f"{created['email']} ({created['role']})",
        decision_maker=admin["full_name"], actor_email=admin["email"], actor_role=admin["role"],
    )
    conn.commit(); conn.close()
    return created


@app.patch("/users/{user_id}")
def users_update(user_id: str, payload: UserUpdate,
                 admin: dict = Depends(auth.require_role("Administrator"))):
    updated = auth.update_user(
        user_id, full_name=payload.full_name, department=payload.department,
        role=payload.role, active=payload.active,
    )
    conn = get_connection()
    log_audit(
        conn, event="USER_UPDATED",
        new_value=f"{updated['email']} active={updated['active']} role={updated['role']}",
        decision_maker=admin["full_name"], actor_email=admin["email"], actor_role=admin["role"],
    )
    conn.commit(); conn.close()
    return updated


@app.post("/users/{user_id}/reset-password")
def users_reset_password(user_id: str, payload: PasswordReset,
                         admin: dict = Depends(auth.require_role("Administrator"))):
    result = auth.reset_password(user_id, payload.new_password)
    conn = get_connection()
    log_audit(
        conn, event="USER_PASSWORD_RESET",
        new_value=f"user_id={user_id}",
        decision_maker=admin["full_name"], actor_email=admin["email"], actor_role=admin["role"],
    )
    conn.commit(); conn.close()
    return result


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
def create_patient(payload: PatientCreate, user: dict = Depends(auth.require_role("Doctor"))):
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
    actor_name = user["full_name"]
    actor_email = user["email"]
    actor_role = user["role"]
    score = calculate_priority(score_breakdown_from_patient(payload.model_dump()))

    log_audit(
        conn,
        event="PATIENT_CREATED",
        event_time=ts,
        patient_id=payload.id,
        patient_name=payload.name,
        diagnosis=payload.diagnosis,
        new_value=f"Registered (age {payload.age}, critical={bool(payload.critical)})",
        decision_maker=actor_name, actor_email=actor_email, actor_role=actor_role,
    )
    log_audit(
        conn,
        event="SCORE_CALCULATED",
        event_time=ts,
        patient_id=payload.id,
        patient_name=payload.name,
        diagnosis=payload.diagnosis,
        new_value=f"{score}/100",
        decision_maker=actor_name, actor_email=actor_email, actor_role=actor_role,
    )
    care = care_level_recommendation(score, bool(payload.critical))
    log_audit(
        conn,
        event="CARE_LEVEL_RECOMMENDED",
        event_time=ts,
        patient_id=payload.id,
        patient_name=payload.name,
        diagnosis=payload.diagnosis,
        recommendation=care["care_level"],
        new_value=care["care_level"],
        decision_maker=actor_name, actor_email=actor_email, actor_role=actor_role,
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
            decision_maker=actor_name, actor_email=actor_email, actor_role=actor_role,
        )
        conn.commit()

    conn.close()
    return {"message": "patient created", "patient_id": payload.id, "priority_score": score}


@app.patch("/patients/{patient_id}")
def update_patient(patient_id: str, payload: PatientUpdate,
                   user: dict = Depends(auth.require_role("Doctor"))):
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
    maker = user["full_name"]
    actor_email = user["email"]
    actor_role = user["role"]
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
            decision_maker=maker, actor_email=actor_email, actor_role=actor_role,
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
            decision_maker=maker, actor_email=actor_email, actor_role=actor_role,
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
                decision_maker=maker, actor_email=actor_email, actor_role=actor_role,
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
            decision_maker=maker, actor_email=actor_email, actor_role=actor_role,
        )

    if new_score is not None:
        care = care_level_recommendation(
            new_score, bool(apply_values.get("critical", current.get("critical", 0)))
        )
        log_audit(
            conn,
            event="CARE_LEVEL_RECOMMENDED",
            event_time=ts,
            patient_id=patient_id,
            patient_name=p_name,
            diagnosis=p_diag,
            recommendation=care["care_level"],
            new_value=care["care_level"],
            decision_maker=maker, actor_email=actor_email, actor_role=actor_role,
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
def allocation(payload: DecisionCreate, user: dict = Depends(auth.require_role("Doctor"))):
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

    actor_name = user["full_name"]
    actor_email = user["email"]
    actor_role = user["role"]

    log_audit(
        conn,
        event=DECISION_EVENTS[payload.decision],
        patient_id=payload.patient_id,
        patient_name=patient["name"],
        diagnosis=patient["diagnosis"],
        recommendation=payload.recommendation,
        decision=payload.decision,
        decision_maker=actor_name, actor_email=actor_email, actor_role=actor_role,
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
                decision_maker=actor_name, actor_email=actor_email, actor_role=actor_role,
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


@app.get("/beds/match/{patient_id}")
def beds_match(patient_id: str):
    result = bed_matching.match_beds_for_patient(patient_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return result


@app.get("/analytics/capacity")
def analytics_capacity():
    """Capacity overview: live ICU/step-down bed counts + aggregated weekly
    demand/refusal from the real operational services_weekly dataset."""
    conn = get_connection()

    def _counts(table):
        rows = conn.execute(f"SELECT status, COUNT(*) AS n FROM {table} GROUP BY status").fetchall()
        d = {r["status"]: r["n"] for r in rows}
        return {
            "available": d.get("available", 0),
            "occupied": d.get("occupied", 0),
            "preparing": d.get("cleaning", 0),
        }

    icu = _counts("icu_beds")
    step_down = _counts("step_down_beds")
    conn.close()

    df = ingestion.load_dataframe("services_weekly")
    services = []
    if not df.empty:
        agg = (
            df.groupby("service")
            .agg(
                avg_available_beds=("available_beds", "mean"),
                total_requested=("patients_request", "sum"),
                total_admitted=("patients_admitted", "sum"),
                total_refused=("patients_refused", "sum"),
            )
            .reset_index()
        )
        for _, r in agg.iterrows():
            req = float(r["total_requested"]) or 1.0
            services.append(
                {
                    "service": r["service"],
                    "avg_available_beds": round(float(r["avg_available_beds"]), 1),
                    "total_requested": int(r["total_requested"]),
                    "total_admitted": int(r["total_admitted"]),
                    "total_refused": int(r["total_refused"]),
                    "refusal_rate_pct": round(float(r["total_refused"]) / req * 100, 1),
                }
            )

    return {
        "loaded": bool(services),
        "icu": icu,
        "step_down": step_down,
        "services": services,
        "note": "Weekly demand/refusal aggregated from the real operational dataset (services_weekly).",
    }


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
                f"No CSV found in data/raw for '{dataset_key}' "
                f"(expected {ingestion.DATASETS[dataset_key].get('filename')})."
            ),
        }
    return {"status": "ok", "inspection": ingestion.inspect_csv(path)}


@app.get("/datasets/{dataset_key}/schema")
def datasets_schema(dataset_key: str):
    if dataset_key not in ingestion.DATASETS:
        raise HTTPException(status_code=404, detail="Unknown dataset")
    return ingestion.get_schema(dataset_key)


@app.post("/datasets/{dataset_key}/import")
def datasets_import(dataset_key: str, user: dict = Depends(auth.require_role("Doctor", "Administrator"))):
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
            decision_maker=user["full_name"], actor_email=user["email"], actor_role=user["role"],
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
def ml_train(payload: TrainRequest, user: dict = Depends(auth.require_role("Doctor", "Administrator"))):
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
            decision_maker=user["full_name"], actor_email=user["email"], actor_role=user["role"],
            reason=result.get("dataset_source"),
        )
        conn.commit()
        conn.close()
    return result


@app.post("/ml/estimate")
def ml_estimate(payload: EstimateRequest):
    return ml_model.estimate_survival(payload.features)
