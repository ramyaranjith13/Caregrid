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
