import os, requests
os.environ["CAREGRID_API_URL"] = "http://127.0.0.1:8000"
from streamlit.testing.v1 import AppTest

API = "http://127.0.0.1:8000"
# read demo password from env file
demo_pw = None; admin_pw = None
for line in open("/app/backend/.env"):
    if line.startswith("SEED_DEMO_PASSWORD"): demo_pw = line.split('"')[1]
    if line.startswith("ADMIN_PASSWORD"): admin_pw = line.split('"')[1]

def login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=8)
    r.raise_for_status(); return r.json()

# 1. Unauthenticated -> login page (no dashboard)
at = AppTest.from_file("/app/app.py", default_timeout=60); at.run()
assert not at.exception, at.exception
labels = [b.label for b in at.button]
assert any("LOGIN" in l for l in labels), f"login button missing: {labels}"
subs = [s.value for s in at.subheader]
assert "Priority Queue" not in subs, "dashboard must NOT show before auth"
print("OK unauthenticated shows login page, dashboard hidden")

# 2. Doctor authenticated -> dashboard + write controls
res = login("doctor1@caregrid.local", demo_pw)
def authed(session_user, token):
    a = AppTest.from_file("/app/app.py", default_timeout=60)
    a.session_state["token"] = token
    a.session_state["auth_user"] = session_user
    a.run()
    return a

ad = authed(res["user"], res["token"])
assert not ad.exception, ad.exception
exp = [e.label for e in ad.expander]
assert any("Register New Patient" in e for e in exp), "Doctor should see registration"
pages = [r for r in ad.radio if "Clinical Dashboard" in list(r.options)][0]
assert "User Management" not in list(pages.options), "Doctor must NOT see User Management"
print("OK Doctor authenticated: dashboard + registration; no admin page")

# 3. Nurse -> view only (no registration expander)
nres = login("nurse@caregrid.local", demo_pw)
an = authed(nres["user"], nres["token"])
assert not an.exception, an.exception
assert not any("Register New Patient" in e.label for e in an.expander), "Nurse must be view-only"
print("OK Nurse view-only")

# 4. Administrator -> has User Management page
ares = login("admin@caregrid.local", admin_pw)
aa = authed(ares["user"], ares["token"])
pages_a = [r for r in aa.radio if "Clinical Dashboard" in list(r.options)][0]
assert "User Management" in list(pages_a.options), "Admin must see User Management"
# open the admin page
pages_a.set_value("User Management").run()
subs_a = [s.value for s in aa.subheader]
assert "Create User" in subs_a and "Users" in subs_a, f"admin page missing: {subs_a}"
print("OK Administrator sees User Management with create/list")

print("\nALL LOGIN/AUTH APPTESTS PASSED")
