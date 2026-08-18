# CareGrid

Hackathon MVP using only:
- Streamlit + HTML/CSS/JavaScript
- FastAPI + Python
- Pandas
- SQLite
- Plotly
- Scikit-learn (future enhancements)
- Git/GitHub

## Run locally

### 1. Create and activate venv
```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Start API (Terminal 1)
```bash
uvicorn backend.main:app --reload
```

### 4. Start Streamlit (Terminal 2)
```bash
source venv/bin/activate
streamlit run app.py
```

Open the URL shown by Streamlit (normally http://localhost:8501).

## Deployment (existing architecture — unchanged)

```
GitHub (main)
   ├── Render            → FastAPI backend
   └── Streamlit Cloud   → Streamlit frontend
```

### Render (FastAPI backend)
- Build: `pip install -r requirements.txt`
- Start: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
- SQLite auto-initialises on startup (safe additive migrations; no destructive reset).

### Streamlit Community Cloud (frontend)
- Main file: `app.py`
- Set environment variable **`CAREGRID_API_URL`** to the Render backend URL
  (e.g. `https://<your-app>.onrender.com`). The backend URL is **never**
  hard-coded — `app.py` reads it from `CAREGRID_API_URL`.

### Datasets
Dataset CSVs and the SQLite DB are git-ignored (`data/raw/`, `data/processed/`,
`data/caregrid.db`). No raw/patient data is committed. Upload dataset CSVs to
`data/raw/` on the host and import them from the Analytics / Validation page.

