# CareGrid — Test Access

CareGrid uses **role selection** (Streamlit session), not password authentication
(per requirement: no hospital-grade auth in the MVP).

On the landing screen choose a role and click **ENTER CAREGRID**:

| Role | Display name | Access |
|------|--------------|--------|
| Doctor | Dr. S. Krishnan | Full: register patients, edit assessment, Accept/Override/Defer, import datasets, train model |
| Nurse | Nurse on Duty | View-only |
| Coordinator | ICU Coordinator | View-only |
| Administrator | Hospital Administrator | View-only |

Backend runs at `CAREGRID_API_URL` (default `http://127.0.0.1:8000`).
No secrets or credentials are stored.
