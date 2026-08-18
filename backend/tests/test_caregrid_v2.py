"""CareGrid V2 backend tests: bed matching, dataset ingestion, and regression."""
import os
import pytest
import requests

BASE_URL = os.environ.get("CAREGRID_API_URL", "http://127.0.0.1:8000").rstrip("/")


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# --- Health & baseline ------------------------------------------------------

def test_health(client):
    r = client.get(f"{BASE_URL}/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


# --- Bed matching -----------------------------------------------------------

def test_bed_match_p002_high(client):
    r = client.get(f"{BASE_URL}/beds/match/P002")
    assert r.status_code == 200
    data = r.json()
    best = data.get("best_match")
    assert best is not None
    assert best["bed_id"] == "ICU-07", f"Expected best ICU-07, got {best['bed_id']}"
    assert best["match_level"] == "HIGH"
    assert best["missing"] == []


def test_bed_match_p003_medium(client):
    r = client.get(f"{BASE_URL}/beds/match/P003")
    assert r.status_code == 200
    data = r.json()
    icu07 = next((m for m in data["icu_matches"] if m["bed_id"] == "ICU-07"), None)
    assert icu07 is not None, "ICU-07 must be in icu_matches"
    assert icu07["match_level"] == "MEDIUM"
    # case-insensitive missing check
    missing_lower = [m.lower() for m in icu07["missing"]]
    assert "vasopressor support" in missing_lower, f"Got missing={icu07['missing']}"
    # required fields present
    for key in ["bed_id","care_level","status","equipment","isolation",
                "compatibility","matched","missing","coverage","match_level","reason"]:
        assert key in icu07, f"Missing field {key}"


def test_bed_match_includes_stepdown_and_excludes_unavailable(client):
    r = client.get(f"{BASE_URL}/beds/match/P002")
    assert r.status_code == 200
    data = r.json()
    sd_ids = [m["bed_id"] for m in data["step_down_matches"]]
    assert len(sd_ids) > 0, "Step-down beds should appear"
    assert all(bid.startswith("SD-") for bid in sd_ids)
    # available status only
    all_matches = data["icu_matches"] + data["step_down_matches"]
    for m in all_matches:
        assert m["status"] == "available", f"Non-available bed leaked: {m}"


def test_bed_match_404(client):
    r = client.get(f"{BASE_URL}/beds/match/DOESNOTEXIST")
    assert r.status_code == 404


def test_bed_match_readonly(client):
    # snapshot patients & beds
    before_patients = client.get(f"{BASE_URL}/patients").json()
    before_beds = client.get(f"{BASE_URL}/icu/beds").json()
    client.get(f"{BASE_URL}/beds/match/P002")
    after_patients = client.get(f"{BASE_URL}/patients").json()
    after_beds = client.get(f"{BASE_URL}/icu/beds").json()
    # compare identifiers/statuses (rank/score should not change from a GET)
    def _key_p(lst):
        return sorted([(p["id"], p.get("status","waiting"), p.get("priority_score")) for p in lst])
    def _key_b(lst):
        return sorted([(b["id"], b["status"]) for b in lst])
    assert _key_p(before_patients) == _key_p(after_patients)
    assert _key_b(before_beds) == _key_b(after_beds)


# --- Dataset ingestion ------------------------------------------------------

@pytest.mark.parametrize("key", ["ops_patients", "services_weekly", "staff", "staff_schedule"])
def test_datasets_import(client, key):
    r = client.post(f"{BASE_URL}/datasets/{key}/import")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") in ("ok", "skipped"), f"{key}: {body}"


def test_dataset_status_all_loaded(client):
    r = client.get(f"{BASE_URL}/datasets/status")
    assert r.status_code == 200
    st = r.json()
    expected_rows = {
        "ops_patients": 1000,
        "services_weekly": 208,
        "staff": 110,
        "staff_schedule": 6552,
    }
    for key, rows in expected_rows.items():
        assert st[key]["loaded"] is True, f"{key} not loaded"
        assert st[key]["rows"] == rows, f"{key} rows={st[key]['rows']} expected {rows}"
        assert st[key]["category"] == "operational"
    assert st["icu_outcome"]["category"] == "ml_synthetic"


def test_ingestion_idempotency(client):
    # already imported previously → should be skipped
    r = client.post(f"{BASE_URL}/datasets/ops_patients/import")
    assert r.status_code == 200
    assert r.json().get("status") == "skipped"


def test_live_patients_not_contaminated(client):
    r = client.get(f"{BASE_URL}/patients")
    assert r.status_code == 200
    patients = r.json()
    ids = {p["id"] for p in patients}
    # demo P001..P005 should be present (some may be deferred though)
    assert {"P001", "P002", "P003"}.issubset(ids) or len(patients) < 50
    # must not have leaked 1000 ops rows
    assert len(patients) < 100, f"Live queue has {len(patients)} patients — leak?"


# --- Existing regression ----------------------------------------------------

def test_patients_ranked_fields(client):
    r = client.get(f"{BASE_URL}/patients")
    assert r.status_code == 200
    patients = r.json()
    assert len(patients) > 0
    p = patients[0]
    for key in ["rank", "priority_score", "scores", "recommendation", "data_complete"]:
        assert key in p, f"Missing {key} in patient response"


def test_patient_create_update_and_defer(client):
    pid = "TEST-BEDMATCH"
    # cleanup by creating fresh (in case previous exists we ignore)
    client.post(f"{BASE_URL}/patients", json={
        "id": pid, "name": "Test Patient", "diagnosis": "Sepsis",
        "age": 55, "waiting_minutes": 30, "critical": True,
        "severity": 20, "dependency": 15, "deterioration": 10,
        "survival_likelihood": 70, "resource": 5,
        "required_resources": "Ventilator, Oxygen"
    })
    # If duplicate, just proceed to update path
    # PATCH — set severity to a different value than current
    current = client.get(f"{BASE_URL}/patients/{pid}").json()
    new_sev = 15.0 if float(current.get("severity", 0)) != 15.0 else 25.0
    up = client.patch(f"{BASE_URL}/patients/{pid}", json={"severity": new_sev})
    assert up.status_code == 200
    body = up.json()
    for key in ["previous_score", "new_score", "previous_rank", "new_rank", "changed_factors"]:
        assert key in body, f"Missing {key} in PATCH response: {body}"

    # DEFER with empty reason → 400
    r = client.post(f"{BASE_URL}/allocation", json={
        "patient_id": pid, "decision": "DEFER", "decision_maker": "Doctor",
        "reason": "", "recommendation": "test"
    })
    assert r.status_code == 400

    # DEFER with reason → ok
    r = client.post(f"{BASE_URL}/allocation", json={
        "patient_id": pid, "decision": "DEFER", "decision_maker": "Doctor",
        "reason": "Test defer", "recommendation": "test"
    })
    assert r.status_code == 200


def test_audit_log_events(client):
    r = client.get(f"{BASE_URL}/audit-log")
    assert r.status_code == 200
    events = {e["event"] for e in r.json()}
    for required in ["PATIENT_CREATED", "ASSESSMENT_UPDATED", "SCORE_CALCULATED",
                     "RANK_CHANGED", "DECISION_DEFERRED"]:
        assert required in events, f"Missing audit event {required}"


# --- ML unchanged -----------------------------------------------------------

def test_ml_status(client):
    r = client.get(f"{BASE_URL}/ml/status")
    assert r.status_code == 200
    data = r.json()
    assert data.get("trained") is True
    assert data.get("model_version") == "v1"


def test_ml_validation(client):
    r = client.get(f"{BASE_URL}/ml/validation")
    assert r.status_code == 200
    data = r.json()
    metrics = data.get("metrics", data)
    for key in ["accuracy", "precision", "recall_sensitivity", "specificity",
                "roc_auc", "confusion_matrix"]:
        assert key in metrics, f"Missing {key} in ml validation metrics"


def test_ml_estimate(client):
    r = client.post(f"{BASE_URL}/ml/estimate", json={
        "features": {"age": 60, "severity": 25, "dependency": 15,
                     "deterioration": 10, "resource": 5}
    })
    assert r.status_code == 200
    data = r.json()
    est = data.get("estimated_survival_likelihood")
    assert est is not None
    assert 0 <= float(est) <= 100
