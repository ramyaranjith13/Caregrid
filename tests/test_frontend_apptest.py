import os
os.environ["CAREGRID_API_URL"] = "http://127.0.0.1:8000"

from streamlit.testing.v1 import AppTest


def run_as(role):
    at = AppTest.from_file("/app/app.py", default_timeout=30)
    at.session_state["role"] = role
    at.session_state["user"] = "Dr. S. Krishnan" if role == "Doctor" else f"{role} on Duty"
    at.run()
    return at


def summarize(at, role):
    print(f"\n===== ROLE: {role} =====")
    assert not at.exception, f"App raised exception: {at.exception}"
    titles = [m.value for m in at.markdown]
    assert any("CAREGRID" in t for t in titles), "CAREGRID title missing"
    print("no exceptions, CAREGRID rendered")
    print("metrics:", [(m.label, m.value) for m in at.metric])
    print("subheaders:", [s.value for s in at.subheader])
    n_expanders = len(at.expander)
    print("expanders:", [e.label for e in at.expander])
    print("dataframes:", len(at.dataframe))
    # Doctor-specific write controls
    has_register = any("Register New Patient" in e.label for e in at.expander)
    has_update = any("Update Clinical Assessment" in e.label for e in at.expander)
    radios = [r.label for r in at.radio]
    print("decision radios:", radios)
    return has_register, has_update, radios


# 1. Doctor
at_doc = run_as("Doctor")
reg, upd, radios = summarize(at_doc, "Doctor")
assert reg, "Doctor must see registration expander"
assert upd, "Doctor must see assessment update expander"
assert any("Decision" in r for r in radios), "Doctor must see decision radio"

# 2. Nurse (view-only)
at_nurse = run_as("Nurse")
reg_n, upd_n, radios_n = summarize(at_nurse, "Nurse")
assert not reg_n, "Nurse must NOT see registration expander"
assert not upd_n, "Nurse must NOT see assessment update expander"
assert not any("Decision" in r for r in radios_n), "Nurse must NOT see decision radio"

# 3. Administrator (view-only)
at_admin = run_as("Administrator")
reg_a, upd_a, radios_a = summarize(at_admin, "Administrator")
assert not reg_a and not upd_a, "Administrator must be view-only"

# 4. Audit trail section present for all
for at, role in [(at_doc, "Doctor"), (at_nurse, "Nurse")]:
    subs = [s.value for s in at.subheader]
    assert "Recent Audit Activity" in subs, f"Audit section missing for {role}"

print("\nALL FRONTEND APPTEST ASSERTIONS PASSED")
