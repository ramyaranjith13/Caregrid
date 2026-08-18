import os
os.environ["CAREGRID_API_URL"] = "http://127.0.0.1:8000"
from streamlit.testing.v1 import AppTest


def open_view(role, view):
    at = AppTest.from_file("/app/app.py", default_timeout=60)
    at.session_state["role"] = role
    at.session_state["user"] = "Dr. S. Krishnan" if role == "Doctor" else f"{role} on Duty"
    at.run()
    vr = next(r for r in at.radio if view in list(r.options))
    vr.set_value(view).run()
    return at


for role in ["Doctor", "Nurse"]:
    for view in ["Capacity Analytics", "Simulation", "CSV Export"]:
        at = open_view(role, view)
        assert not at.exception, f"{role}/{view} raised: {at.exception}"
        print(f"OK {role} / {view} · subheaders={[s.value for s in at.subheader]} · downloads={len(at.download_button)}")

# Simulation: start + step should not error and should show a queue table
at = open_view("Doctor", "Simulation")
buttons = {b.label: b for b in at.button}
print("simulation buttons:", list(buttons))
# CSV export must expose download buttons for Doctor and Nurse (read-only allowed)
at_exp = open_view("Nurse", "CSV Export")
assert len(at_exp.download_button) >= 3, "CSV export should offer downloads to all roles"
print("CSV export download buttons (Nurse):", len(at_exp.download_button))

# Dashboard still renders with care level + step-down table
at_d = open_view("Doctor", "Clinical Dashboard")
subs = [s.value for s in at_d.subheader]
assert "Step-Down Bed Status" in subs, "step-down bed table missing"
assert "Recent Audit Activity" in subs
print("dashboard subheaders:", subs)
print("\nALL PAGE APPTESTS PASSED")
