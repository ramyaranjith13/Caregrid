# CareGrid Auth Testing

Stack: FastAPI (port 8000) + SQLite + Streamlit. Auth = email/password (bcrypt) + JWT Bearer.
Read credentials from `/app/memory/test_credentials.md`.

## SQLite verification
```
sqlite3 /app/data/caregrid.db "SELECT email, role, active, substr(password_hash,1,4) FROM users_auth;"
```
Verify hashes start with `$2b$` (bcrypt). Table `users_auth`; brute-force in `login_attempts`.

## API testing (Bearer token, NOT cookies — Streamlit sends Authorization header)
```
API=http://127.0.0.1:8000
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"doctor1@caregrid.local","password":"<demo_pw>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s $API/auth/me -H "Authorization: Bearer $TOKEN"
```

Expect:
- login returns {token, user}; /auth/me returns same user.
- wrong password / unknown email => 401 (generic "Invalid email or password").
- inactive account => 403.
- 5 rapid wrong passwords for same email => 429 lockout (15 min window).
- POST /patients, PATCH /patients/{id}, POST /allocation without Bearer => 401; with Nurse/Coordinator/Administrator token => 403 "Access Denied"; with Doctor token => success.
- /users* require Administrator token (else 403).
- Audit rows carry decision_maker (full_name), actor_email, actor_role from the token.
