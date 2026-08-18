"""Authentication for CareGrid — custom email/password + JWT (bcrypt hashing).

Streamlit is a server-side frontend, so the JWT is stored in Streamlit
session_state and sent as an Authorization: Bearer header (no browser
cookies). FastAPI verifies the token and enforces roles.
"""

import os
import re
import uuid
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request

from .database import get_connection

JWT_ALGORITHM = "HS256"
ACCESS_TTL_HOURS = 12
MAX_FAILED = 5
LOCKOUT_MINUTES = 15
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_ROLES = {"Doctor", "Nurse", "Coordinator", "Administrator"}


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def get_jwt_secret() -> str:
    return os.environ["JWT_SECRET"]


# ---- password hashing ----
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def validate_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email or ""))


def validate_password(password: str):
    if not password or len(password) < 8:
        return "Password must be at least 8 characters."
    return None


# ---- JWT ----
def create_access_token(user: dict) -> str:
    payload = {
        "sub": user["user_id"],
        "email": user["email"],
        "role": user["role"],
        "exp": _now() + timedelta(hours=ACCESS_TTL_HOURS),
        "type": "access",
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def _public_user(row) -> dict:
    d = dict(row)
    d.pop("password_hash", None)
    d["active"] = bool(d.get("active", 1))
    return d


def get_user_by_email(email: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users_auth WHERE email = ?", (email.strip().lower(),)
    ).fetchone()
    conn.close()
    return row


def get_user_by_id(user_id: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users_auth WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row


# ---- brute-force protection ----
def _record_failed(email: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO login_attempts(email, attempt_time) VALUES (?, ?)",
        (email.strip().lower(), _iso(_now())),
    )
    conn.commit()
    conn.close()


def _clear_failed(email: str):
    conn = get_connection()
    conn.execute("DELETE FROM login_attempts WHERE email = ?", (email.strip().lower(),))
    conn.commit()
    conn.close()


def is_locked_out(email: str) -> bool:
    cutoff = _iso(_now() - timedelta(minutes=LOCKOUT_MINUTES))
    conn = get_connection()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM login_attempts WHERE email = ? AND attempt_time > ?",
        (email.strip().lower(), cutoff),
    ).fetchone()["n"]
    conn.close()
    return n >= MAX_FAILED


def authenticate(email: str, password: str) -> dict:
    """Return public user dict + token, or raise HTTPException (generic messages)."""
    email_norm = (email or "").strip().lower()
    if not email_norm or not password:
        raise HTTPException(status_code=400, detail="Email and password are required.")

    if is_locked_out(email_norm):
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Please try again in a few minutes.",
        )

    row = get_user_by_email(email_norm)
    if row is None or not verify_password(password, row["password_hash"]):
        _record_failed(email_norm)
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not bool(row["active"]):
        raise HTTPException(status_code=403, detail="Account is inactive. Contact an administrator.")

    _clear_failed(email_norm)
    conn = get_connection()
    conn.execute(
        "UPDATE users_auth SET last_login = ? WHERE user_id = ?",
        (_iso(_now()), row["user_id"]),
    )
    conn.commit()
    conn.close()

    user = _public_user(row)
    return {"token": create_access_token(user), "user": user}


# ---- FastAPI dependencies ----
def get_current_user(request: Request) -> dict:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = auth_header[7:]
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    row = get_user_by_id(payload.get("sub"))
    if row is None:
        raise HTTPException(status_code=401, detail="User not found")
    if not bool(row["active"]):
        raise HTTPException(status_code=403, detail="Account is inactive.")
    return _public_user(row)


def require_role(*roles):
    def _dep(user: dict = Depends(get_current_user)) -> dict:
        if roles and user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Access Denied")
        return user
    return _dep


# ---- user management ----
def create_user(full_name, email, password, role, department="", active=True):
    email_norm = (email or "").strip().lower()
    if not full_name or not full_name.strip():
        raise HTTPException(status_code=400, detail="Full name is required.")
    if not validate_email(email_norm):
        raise HTTPException(status_code=400, detail="Invalid email format.")
    pw_err = validate_password(password)
    if pw_err:
        raise HTTPException(status_code=400, detail=pw_err)
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Role must be one of {sorted(VALID_ROLES)}.")
    if get_user_by_email(email_norm) is not None:
        raise HTTPException(status_code=400, detail="A user with this email already exists.")

    user_id = f"USR-{uuid.uuid4().hex[:10]}"
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO users_auth(user_id, full_name, email, password_hash, role,
                          department, active, created_at, last_login)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            user_id,
            full_name.strip(),
            email_norm,
            hash_password(password),
            role,
            (department or "").strip(),
            1 if active else 0,
            _iso(_now()),
        ),
    )
    conn.commit()
    conn.close()
    return _public_user(get_user_by_id(user_id))


def list_users():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM users_auth ORDER BY role, full_name"
    ).fetchall()
    conn.close()
    return [_public_user(r) for r in rows]


def update_user(user_id, *, full_name=None, department=None, role=None, active=None):
    row = get_user_by_id(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    fields, values = [], []
    if full_name is not None:
        fields.append("full_name = ?"); values.append(full_name.strip())
    if department is not None:
        fields.append("department = ?"); values.append(department.strip())
    if role is not None:
        if role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail="Invalid role.")
        fields.append("role = ?"); values.append(role)
    if active is not None:
        fields.append("active = ?"); values.append(1 if active else 0)
    if not fields:
        return _public_user(row)
    values.append(user_id)
    conn = get_connection()
    conn.execute(f"UPDATE users_auth SET {', '.join(fields)} WHERE user_id = ?", values)
    conn.commit()
    conn.close()
    return _public_user(get_user_by_id(user_id))


def reset_password(user_id, new_password):
    row = get_user_by_id(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    pw_err = validate_password(new_password)
    if pw_err:
        raise HTTPException(status_code=400, detail=pw_err)
    conn = get_connection()
    conn.execute(
        "UPDATE users_auth SET password_hash = ? WHERE user_id = ?",
        (hash_password(new_password), user_id),
    )
    conn.commit()
    conn.close()
    return {"message": "password reset"}


# ---- idempotent seeding ----
def seed_users():
    """Create admin + demo accounts from env. Idempotent and safe to re-run."""
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@caregrid.local").strip().lower()
    admin_pw = os.environ.get("ADMIN_PASSWORD")
    demo_pw = os.environ.get("SEED_DEMO_PASSWORD")
    if not admin_pw or not demo_pw:
        # No credentials configured — skip seeding rather than hard-code anything.
        return

    accounts = [
        (admin_email, "CareGrid Administrator", admin_pw, "Administrator", "Administration"),
        ("doctor1@caregrid.local", "Dr. S. Krishnan", demo_pw, "Doctor", "ICU"),
        ("doctor2@caregrid.local", "Dr. A. Fernandes", demo_pw, "Doctor", "Emergency"),
        ("nurse@caregrid.local", "Nurse on Duty", demo_pw, "Nurse", "ICU"),
        ("coordinator@caregrid.local", "ICU Coordinator", demo_pw, "Coordinator", "Operations"),
    ]
    conn = get_connection()
    for email, name, pw, role, dept in accounts:
        existing = conn.execute(
            "SELECT user_id, password_hash FROM users_auth WHERE email = ?", (email,)
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO users_auth(user_id, full_name, email, password_hash, role,
                                  department, active, created_at, last_login)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL)
                """,
                (f"USR-{uuid.uuid4().hex[:10]}", name, email, hash_password(pw),
                 role, dept, _iso(_now())),
            )
        elif not verify_password(pw, existing["password_hash"]):
            conn.execute(
                "UPDATE users_auth SET password_hash = ? WHERE email = ?",
                (hash_password(pw), email),
            )
    conn.commit()
    conn.close()
