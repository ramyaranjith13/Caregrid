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

## Implemented — Session 2: Real ops datasets + Bed Matching (2026-08-18)
User decisions: keep survival model synthetic that round; ingest 4 real uploaded CSVs into separate tables; build ONLY Bed Matching.

- **Real ops datasets**: `patients.csv` (1000), `services_weekly.csv` (208), `staff.csv` (110), `staff_schedule.csv` (6552) → tables `ops_patients`, `services_weekly_capacity`, `staff_directory`, `staff_schedule`. Idempotent (SHA-256), separate from live `patients`.
- **Bed Matching** (`backend/services/bed_matching.py`, `GET /beds/match/{patient_id}`): required_resources vs available ICU + step-down beds → HIGH/MEDIUM/LOW + missing + reason. Read-only; `best_match=None` on incomplete data.

## Implemented — Session 3: FINAL completion (2026-08-18)
- **Real ICU outcome dataset**: uploaded X_train_2025.csv + y_train_2025.csv (PhysioNet death-classification), merged → `data/raw/icu_outcome_real.csv`, table `icu_outcome_real` (3600 rows, target `in_hospital_death`). Category ml_real.
- **Real survival model (v2)**: LogisticRegression (median impute + scale, class_weight=balanced), 119 features (recordid/id/all-NaN/constant excluded), 2700/900 split. Real metrics: Acc 0.78, Precision 0.36, Recall 0.72, Specificity 0.79, ROC-AUC 0.846. Supporting estimate only. Model prefers real dataset over synthetic (`resolve_dataset_key`).
- **Care Level** (`prioritization.care_level_recommendation`, prototype thresholds): ICU Candidate / Step-Down Candidate / Ward-Continue Care; shown in Selected Patient; logged as CARE_LEVEL_RECOMMENDED on create/update.
- **Capacity Analytics** (`GET /analytics/capacity` + page): live ICU/step-down counts + real services_weekly aggregation (admitted/refused/refusal-rate by service).
- **Simulation Mode** (frontend, synthetic only, never touches live DB): Start/Next/Reset, event timeline, live re-ranking via `/priority/calculate`.
- **CSV Export** page: priority queue, ranking summary, audit trail, ICU beds, step-down beds, capacity summary (all roles).
- **Dashboard polish**: 6-metric top row incl. Step-Down Available + alerts banner; step-down bed table; sidebar nav (Dashboard / Analytics / Capacity / Simulation / Export).
- **Verified**: testing_agent 35/35 backend pass; Streamlit AppTests (dashboard, analytics, all pages) pass; pyflakes clean; `/health` + `/docs` 200; requirements valid.

## Backlog (not yet built)
- P1: Step-down / care-level recommendation UI, per-patient bed compatibility matching, near-tie UI explanation.
- P1: Simulation mode; capacity forecast (ICU + step-down); export reports (CSV).
- P2: Retrain on real Kaggle data once uploaded; hospital-bed dataset capacity analytics.
- Deployment: NOT done per instruction ("do not deploy yet").
