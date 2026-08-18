"""CareGrid auth + RBAC + admin user-management + audit attribution tests.

Runs against the LOCAL FastAPI at http://127.0.0.1:8000 (NOT the k8s preview URL).
Credentials sourced from /app/memory/test_credentials.md — do not hardcode outside constants.
"""
import os
import uuid
import sqlite3
import pytest
import requests

BASE = os.environ.get("CAREGRID_API", "http://127.0.0.1:8000")
DB_PATH = "/app/data/caregrid.db"

ADMIN_EMAIL = "admin@caregrid.local"
ADMIN_PW = "lTKmFJimWU_i6rMY"
DOCTOR1_EMAIL = "doctor1@caregrid.local"
DOCTOR2_EMAIL = "doctor2@caregrid.local"
NURSE_EMAIL = "nurse@caregrid.local"
COORD_EMAIL = "coordinator@caregrid.local"
DEMO_PW = "VCo-PtjTNG9Wzx0U"


# ---------------- helpers ----------------
def _login(email, password):
    return requests.post(f"{BASE}/auth/login", json={"email": email, "password": password})


def _token(email, password):
    r = _login(email, password)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return r.json()["token"]


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _clear_attempts(email=None):
    conn = sqlite3.connect(DB_PATH)
    if email:
        conn.execute("DELETE FROM login_attempts WHERE email = ?", (email,))
    else:
        conn.execute("DELETE FROM login_attempts")
    conn.commit()
    conn.close()


# ---------------- fixtures ----------------
@pytest.fixture(scope="session")
def admin_token():
    _clear_attempts(ADMIN_EMAIL)
    return _token(ADMIN_EMAIL, ADMIN_PW)


@pytest.fixture(scope="session")
def doctor1_token():
    _clear_attempts(DOCTOR1_EMAIL)
    return _token(DOCTOR1_EMAIL, DEMO_PW)


@pytest.fixture(scope="session")
def doctor2_token():
    _clear_attempts(DOCTOR2_EMAIL)
    return _token(DOCTOR2_EMAIL, DEMO_PW)


@pytest.fixture(scope="session")
def nurse_token():
    _clear_attempts(NURSE_EMAIL)
    return _token(NURSE_EMAIL, DEMO_PW)


@pytest.fixture(scope="session")
def coord_token():
    _clear_attempts(COORD_EMAIL)
    return _token(COORD_EMAIL, DEMO_PW)


# ---------------- Login / /auth/me ----------------
class TestLogin:
    def test_doctor1_login_and_me(self, doctor1_token):
        r = requests.get(f"{BASE}/auth/me", headers=_hdr(doctor1_token))
        assert r.status_code == 200
        u = r.json()
        assert u["email"] == DOCTOR1_EMAIL
        assert u["role"] == "Doctor"
        assert u["department"] == "ICU"
        assert u["full_name"] == "Dr. S. Krishnan"
        assert "password_hash" not in u

    def test_doctor2_login_distinct_identity(self, doctor2_token):
        r = requests.get(f"{BASE}/auth/me", headers=_hdr(doctor2_token))
        assert r.status_code == 200
        u = r.json()
        assert u["email"] == DOCTOR2_EMAIL
        assert u["full_name"] == "Dr. A. Fernandes"
        assert u["department"] == "Emergency"
        assert "password_hash" not in u

    def test_wrong_password_generic_401(self):
        _clear_attempts(DOCTOR1_EMAIL)
        r = _login(DOCTOR1_EMAIL, "totally-wrong-pw")
        assert r.status_code == 401
        body = r.text.lower()
        # must NOT reveal that the email exists
        assert "invalid email or password" in body
        assert "not found" not in body and "no such" not in body
        _clear_attempts(DOCTOR1_EMAIL)

    def test_unknown_email_generic_401(self):
        r = _login("no-such-user@nowhere.tld", "whatever12345")
        assert r.status_code == 401
        assert "invalid email or password" in r.text.lower()
        _clear_attempts("no-such-user@nowhere.tld")

    def test_missing_fields_400(self):
        r = _login("", "")
        assert r.status_code == 400

    def test_logout_ok(self, doctor1_token):
        r = requests.post(f"{BASE}/auth/logout", headers=_hdr(doctor1_token))
        assert r.status_code == 200


# ---------------- Brute-force lockout ----------------
class TestBruteForce:
    def test_lockout_after_5_failures(self):
        throwaway = "bruteforce-test@caregrid.local"
        _clear_attempts(throwaway)
        for i in range(5):
            r = _login(throwaway, "wrong")
            assert r.status_code == 401, f"attempt {i}: {r.status_code}"
        r6 = _login(throwaway, "wrong")
        assert r6.status_code == 429, f"expected 429 lockout, got {r6.status_code} {r6.text}"
        _clear_attempts(throwaway)


# ---------------- Inactive user ----------------
class TestInactiveUser:
    def test_inactive_user_cannot_login(self, admin_token):
        # find nurse user_id
        users = requests.get(f"{BASE}/users", headers=_hdr(admin_token)).json()
        nurse = next(u for u in users if u["email"] == NURSE_EMAIL)
        try:
            # deactivate
            r = requests.patch(
                f"{BASE}/users/{nurse['user_id']}",
                headers=_hdr(admin_token),
                json={"active": False},
            )
            assert r.status_code == 200
            _clear_attempts(NURSE_EMAIL)
            r2 = _login(NURSE_EMAIL, DEMO_PW)
            assert r2.status_code == 403
            assert "inactive" in r2.text.lower()
        finally:
            # re-activate
            requests.patch(
                f"{BASE}/users/{nurse['user_id']}",
                headers=_hdr(admin_token),
                json={"active": True},
            )
            _clear_attempts(NURSE_EMAIL)


# ---------------- RBAC on clinical writes ----------------
class TestRBAC:
    def _patient_payload(self, pid):
        return {
            "id": pid,
            "name": "TEST_Patient_RBAC",
            "diagnosis": "TEST_dx",
            "age": 55,
            "waiting_minutes": 10,
            "critical": False,
            "severity": 20,
            "dependency": 10,
            "deterioration": 10,
            "survival_likelihood": 70,
            "resource": 5,
            "required_resources": "",
            "decision_maker": "Doctor",
        }

    def test_post_patients_no_token_401(self):
        r = requests.post(f"{BASE}/patients", json=self._patient_payload("TESTX_NOTOKEN"))
        assert r.status_code == 401

    def test_post_patients_nurse_403(self, nurse_token):
        r = requests.post(
            f"{BASE}/patients",
            json=self._patient_payload("TESTX_NURSE"),
            headers=_hdr(nurse_token),
        )
        assert r.status_code == 403
        assert "access denied" in r.text.lower()

    def test_post_patients_coordinator_403(self, coord_token):
        r = requests.post(
            f"{BASE}/patients",
            json=self._patient_payload("TESTX_COORD"),
            headers=_hdr(coord_token),
        )
        assert r.status_code == 403

    def test_post_patients_admin_403(self, admin_token):
        r = requests.post(
            f"{BASE}/patients",
            json=self._patient_payload("TESTX_ADMIN"),
            headers=_hdr(admin_token),
        )
        assert r.status_code == 403

    def test_allocation_no_token_401(self):
        r = requests.post(
            f"{BASE}/allocation",
            json={
                "patient_id": "P001",
                "decision": "ACCEPT",
                "decision_maker": "x",
                "recommendation": "test",
            },
        )
        assert r.status_code == 401

    def test_allocation_nurse_403(self, nurse_token):
        r = requests.post(
            f"{BASE}/allocation",
            headers=_hdr(nurse_token),
            json={
                "patient_id": "P001",
                "decision": "ACCEPT",
                "decision_maker": "x",
                "recommendation": "test",
            },
        )
        assert r.status_code == 403

    def test_reads_still_open(self, nurse_token):
        # reads are not role-restricted — nurse token OK, and endpoints work unauth too
        for path in ["/patients", "/icu/beds", "/step-down/beds", "/analytics/capacity", "/audit-log"]:
            r = requests.get(f"{BASE}{path}", headers=_hdr(nurse_token))
            assert r.status_code == 200, f"{path} => {r.status_code}"


# ---------------- Doctor happy path + audit attribution ----------------
class TestDoctorFlowAndAudit:
    def test_doctor_create_patient_decide_and_audit(self, doctor1_token):
        pid = "TESTAU_" + uuid.uuid4().hex[:6]
        payload = {
            "id": pid,
            "name": "TEST_Audit_Patient",
            "diagnosis": "TEST_dx",
            "age": 60,
            "waiting_minutes": 5,
            "critical": False,
            "severity": 22,
            "dependency": 12,
            "deterioration": 11,
            "survival_likelihood": 75,
            "resource": 6,
            "required_resources": "",
            "decision_maker": "Doctor",
        }
        r = requests.post(f"{BASE}/patients", headers=_hdr(doctor1_token), json=payload)
        assert r.status_code in (200, 201), r.text

        # OVERRIDE without reason => 400
        bad = requests.post(
            f"{BASE}/allocation",
            headers=_hdr(doctor1_token),
            json={
                "patient_id": pid,
                "decision": "OVERRIDE",
                "decision_maker": "Dr. S. Krishnan",
                "reason": "   ",
                "recommendation": "ICU",
            },
        )
        assert bad.status_code == 400

        # Valid ACCEPT
        dec = requests.post(
            f"{BASE}/allocation",
            headers=_hdr(doctor1_token),
            json={
                "patient_id": pid,
                "decision": "ACCEPT",
                "decision_maker": "Dr. S. Krishnan",
                "reason": "clinically indicated",
                "recommendation": "ICU",
            },
        )
        assert dec.status_code == 200, dec.text

        # Verify audit rows carry actor attribution
        log = requests.get(f"{BASE}/audit-log", params={"patient_id": pid}).json()
        events = {row["event"] for row in log}
        # Expected event types from spec
        for expected in {"PATIENT_CREATED", "SCORE_CALCULATED", "CARE_LEVEL_RECOMMENDED",
                         "RECOMMENDATION_GENERATED", "DECISION_ACCEPTED"}:
            assert expected in events, f"missing audit event {expected}; got {events}"
        for row in log:
            if row["event"] in {"PATIENT_CREATED", "DECISION_ACCEPTED"}:
                assert row.get("decision_maker") == "Dr. S. Krishnan", row
                assert row.get("actor_email") == DOCTOR1_EMAIL, row
                assert row.get("actor_role") == "Doctor", row

        # cleanup — delete patient row directly (no delete endpoint)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM patients WHERE id = ?", (pid,))
        conn.execute("DELETE FROM audit_logs WHERE patient_id = ?", (pid,))
        conn.commit()
        conn.close()


# ---------------- Admin user management ----------------
class TestAdminUserMgmt:
    def test_users_list_requires_admin_doctor_403(self, doctor1_token):
        r = requests.get(f"{BASE}/users", headers=_hdr(doctor1_token))
        assert r.status_code == 403

    def test_users_list_no_password_hash(self, admin_token):
        r = requests.get(f"{BASE}/users", headers=_hdr(admin_token))
        assert r.status_code == 200
        for u in r.json():
            assert "password_hash" not in u

    def test_create_validation_and_crud(self, admin_token):
        # invalid email
        r = requests.post(f"{BASE}/users", headers=_hdr(admin_token), json={
            "full_name": "TEST U", "email": "not-an-email", "password": "abcdefgh",
            "role": "Nurse", "department": "ICU"
        })
        assert r.status_code == 400

        # short password
        r = requests.post(f"{BASE}/users", headers=_hdr(admin_token), json={
            "full_name": "TEST U", "email": f"testu_{uuid.uuid4().hex[:6]}@caregrid.local",
            "password": "short", "role": "Nurse", "department": "ICU"
        })
        assert r.status_code == 400

        # valid create
        email = f"testu_{uuid.uuid4().hex[:6]}@caregrid.local"
        r = requests.post(f"{BASE}/users", headers=_hdr(admin_token), json={
            "full_name": "TEST_ New User", "email": email, "password": "OldPass123!",
            "role": "Nurse", "department": "ICU"
        })
        assert r.status_code in (200, 201), r.text
        uid = r.json()["user_id"]

        # duplicate email => 400
        dup = requests.post(f"{BASE}/users", headers=_hdr(admin_token), json={
            "full_name": "TEST_ Dup", "email": email, "password": "OldPass123!",
            "role": "Nurse", "department": "ICU"
        })
        assert dup.status_code == 400

        try:
            # PATCH update
            r = requests.patch(f"{BASE}/users/{uid}", headers=_hdr(admin_token),
                               json={"full_name": "TEST_ Updated", "department": "Ward", "role": "Coordinator"})
            assert r.status_code == 200
            body = r.json()
            assert body["full_name"] == "TEST_ Updated"
            assert body["role"] == "Coordinator"

            # reset password
            _clear_attempts(email)
            new_pw = "BrandNewPw123!"
            r = requests.post(f"{BASE}/users/{uid}/reset-password", headers=_hdr(admin_token),
                              json={"new_password": new_pw})
            assert r.status_code == 200
            # old password fails
            assert _login(email, "OldPass123!").status_code == 401
            _clear_attempts(email)
            # new password works
            assert _login(email, new_pw).status_code == 200
        finally:
            # cleanup — deactivate + drop from DB
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM users_auth WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM login_attempts WHERE email = ?", (email,))
            conn.commit()
            conn.close()


# ---------------- Password storage security ----------------
class TestPasswordStorage:
    def test_hashes_are_bcrypt(self):
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT email, password_hash FROM users_auth").fetchall()
        conn.close()
        assert rows, "no users_auth rows"
        for email, h in rows:
            assert h.startswith("$2b$"), f"{email}: hash does not start with $2b$: {h[:6]}"

    def test_me_never_returns_hash(self, doctor1_token):
        r = requests.get(f"{BASE}/auth/me", headers=_hdr(doctor1_token))
        assert "password_hash" not in r.text
        assert "$2b$" not in r.text


# ---------------- Prioritization weights unchanged ----------------
class TestPrioritizationWeights:
    def test_weights_sum_and_extremes(self):
        # Max scores should produce 100 given weights 35/25/20/10/10
        r = requests.post(f"{BASE}/priority/calculate", json={
            "severity": 35, "dependency": 25, "deterioration": 20,
            "waiting": 10, "resource": 10
        })
        assert r.status_code == 200
        assert abs(r.json()["score"] - 100.0) < 0.5, r.json()

        r0 = requests.post(f"{BASE}/priority/calculate", json={
            "severity": 0, "dependency": 0, "deterioration": 0,
            "waiting": 0, "resource": 0
        })
        assert r0.status_code == 200
        assert r0.json()["score"] == 0
