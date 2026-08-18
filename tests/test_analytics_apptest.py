import os
os.environ["CAREGRID_API_URL"] = "http://127.0.0.1:8000"

from streamlit.testing.v1 import AppTest


def analytics_as(role):
    at = AppTest.from_file("/app/app.py", default_timeout=60)
    at.session_state["role"] = role
    at.session_state["user"] = "Dr. S. Krishnan" if role == "Doctor" else f"{role} on Duty"
    at.run()
    # find the sidebar view selector by its options
    view_radio = next(
        r for r in at.radio if "Analytics / Validation" in list(r.options)
    )
    view_radio.set_value("Analytics / Validation").run()
    return at


def check(at, role, expect_buttons):
    print(f"\n===== ANALYTICS as {role} =====")
    assert not at.exception, f"exception: {at.exception}"
    subs = [s.value for s in at.subheader]
    print("subheaders:", subs)
    assert "Dataset Status" in subs, "Dataset Status missing"
    assert "Survival Model" in subs, "Survival Model missing"
    labels = [b.label for b in at.button]
    print("buttons:", labels)
    has_train = any("Train survival model" in l for l in labels)
    has_import = any("Import" in l for l in labels)
    if expect_buttons:
        assert has_train and has_import, f"{role} should have import+train buttons"
    else:
        assert not has_train and not has_import, f"{role} must be view-only (no write buttons)"
    metrics = [(m.label, m.value) for m in at.metric]
    print("metrics:", metrics)
    return subs, metrics


at_doc = analytics_as("Doctor")
subs_d, metrics_d = check(at_doc, "Doctor", expect_buttons=True)
assert "Validation Metrics" in subs_d, "Validation Metrics should show (model trained)"
assert any(l == "ROC-AUC" for l, _ in metrics_d), "ROC-AUC metric should be shown"

at_nurse = analytics_as("Nurse")
check(at_nurse, "Nurse", expect_buttons=False)

at_admin = analytics_as("Administrator")
check(at_admin, "Administrator", expect_buttons=False)

print("\nALL ANALYTICS APPTEST ASSERTIONS PASSED")
