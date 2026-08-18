# CareGrid — Test Credentials (LOCAL ONLY — git-ignored, never committed)

Authentication: custom email/password (bcrypt) + JWT Bearer token.
Backend: http://127.0.0.1:8000 · Passwords live only in backend/.env (git-ignored).

## Accounts (seeded idempotently on startup)
| Email | Password | Role | Department |
|-------|----------|------|------------|
| admin@caregrid.local | lTKmFJimWU_i6rMY | Administrator | Administration |
| doctor1@caregrid.local | VCo-PtjTNG9Wzx0U | Doctor | ICU |
| doctor2@caregrid.local | VCo-PtjTNG9Wzx0U | Doctor | Emergency |
| nurse@caregrid.local | VCo-PtjTNG9Wzx0U | Nurse | ICU |
| coordinator@caregrid.local | VCo-PtjTNG9Wzx0U | Coordinator | Operations |

## Auth endpoints
POST /auth/login {email,password} -> {token, user}
GET  /auth/me            (Bearer)
POST /auth/logout        (Bearer)
GET  /users              (Administrator)
POST /users              (Administrator)
PATCH /users/{id}        (Administrator)
POST /users/{id}/reset-password (Administrator)

Send the token as: Authorization: Bearer <token>
Doctor role required for: POST /patients, PATCH /patients/{id}, POST /allocation, dataset import, ML train.
