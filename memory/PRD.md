# CareGrid V2 — PRD / Build Log

## Problem statement
Incrementally upgrade the EXISTING CareGrid repository (Streamlit + FastAPI +
SQLite + Pandas + Plotly + scikit-learn). Explainable ICU decision-support
prototype. The doctor is always the final decision-maker; the system never
autonomously allocates ICU beds. Deployment: Render (FastAPI) + Streamlit Cloud.
Frontend uses env var `CAREGRID_API_URL` (never hard-coded).

## Architecture (unchanged)
`app.py` (Streamlit) → FastAPI (`backend/main.py`) → SQLite (`data/caregrid.db`).
Scoring/ranking: `backend/services/prioritization.py`. Prototype 100-pt model
(severity 35 / dependency 25 / deterioration 20 / waiting 10 / resource 10) —
**weights unchanged**.

## Implemented — Phase 1 (2026-08-18)
- **Patient registration UI** (Doctor-only) incl. age; POST `/patients` → SQLite; auto score + queue placement.
- **Structured clinical assessment** fields; editable by Doctor only; Nurse/Coordinator/Admin view-only.
- **Clinical deterioration update**: PATCH `/patients/{id}` recalculates score, re-ranks queue, logs old/new.
- **Dynamic re-ranking** on create / update / defer.
- **Rich audit trail**: central `log_audit()`; events PATIENT_CREATED, ASSESSMENT_UPDATED, SCORE_CALCULATED, RANK_CHANGED, RECOMMENDATION_GENERATED, DECISION_ACCEPTED/OVERRIDDEN/DEFERRED (+ DATASET_IMPORTED, MODEL_TRAINED). Columns: patient_name, event, previous_value, new_value.
- **Audit Trail UI** section + **"What Changed?"** view (reconstructed from audit).
- **Data-quality warnings** (missing required clinical fields) in queue alerts, selected patient, and registration.

## Implemented — Dataset & ML (2026-08-18)
- **Ingestion pipeline** (`backend/services/ingestion.py`): column-agnostic (schema discovered from the actual CSV — no assumed column names), dedupe, missing-row cleaning, rejected-row counts, idempotent via SHA-256 file hash, saves processed CSV, records `dataset_imports` metadata.
- **New SQLite tables** (additive, safe): `step_down_beds`, `dataset_imports`, `model_metadata`; per-dataset tables `hospital_bed_dataset` / `icu_outcome_dataset` created at import from real columns.
- **Survival model** (`backend/services/ml_model.py`): LogisticRegression pipeline (impute+scale) trained on imported ICU outcome data; auto-detects binary target (name-hint aware); metrics = accuracy/precision/recall/specificity/ROC-AUC/confusion matrix/ROC curve/feature importance from a held-out test split (no fabricated metrics). Provides supporting `Estimated survival likelihood: XX%` only — never allocates/discharges/transfers.
- **Analytics / Validation** page (sidebar nav): Dataset Status, model status, import/train buttons (Doctor-only), metrics + Plotly confusion matrix / ROC / feature importance. Clearly separates LIVE/DEMO vs TRAINING/VALIDATION data and labels SYNTHETIC vs Kaggle source.
- **Synthetic demo data** (`scripts/generate_synthetic_datasets.py`) written to `data/raw/` (SYNTHETIC_*.csv), clearly labelled — pipeline is demonstrable now.

### IMPORTANT — Kaggle datasets require MANUAL upload
Anonymous Kaggle download returns HTTP 403. Real CSVs must be downloaded and
placed in `data/raw/`, then imported from the Analytics page. See `data/README.md`.

## API endpoints
GET `/health`, `/patients`, `/patients/{id}`, `/icu/beds`, `/step-down/beds`, `/audit-log[?patient_id=]`;
POST `/patients`, `/allocation`, `/priority/calculate`; PATCH `/patients/{id}`;
GET `/datasets/status`, `/datasets/{key}/inspect`, `/datasets/{key}/schema`, `/ml/status`, `/ml/validation`;
POST `/datasets/{key}/import`, `/ml/train`, `/ml/estimate`.

## Tests
- Backend: curl suite (create, duplicate 400, range 422, 404, update+rerank, no-op, defer, audit, ingestion idempotency, train, estimate).
- Frontend: Streamlit `AppTest` — role gating (Doctor write vs Nurse/Coord/Admin view-only), dashboard render, analytics render + metrics. Files: `tests/test_frontend_apptest.py`, `tests/test_analytics_apptest.py`.
- NOTE: Streamlit does not render through the preview proxy (websocket) — verified via AppTest instead. Run locally: `uvicorn backend.main:app` + `streamlit run app.py`.

## Backlog (not yet built)
- P1: Step-down / care-level recommendation UI, per-patient bed compatibility matching, near-tie UI explanation.
- P1: Simulation mode; capacity forecast (ICU + step-down); export reports (CSV).
- P2: Retrain on real Kaggle data once uploaded; hospital-bed dataset capacity analytics.
- Deployment: NOT done per instruction ("do not deploy yet").
