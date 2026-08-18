import os

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

API_URL = os.getenv("CAREGRID_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="CareGrid", page_icon="❤", layout="wide")

st.markdown(
    """
    <style>
    .main { background: #f6f9fc; }
    .cg-title { font-size: 34px; font-weight: 800; color: #102a43; margin-bottom: 0; }
    .cg-sub { color: #486581; margin-top: 0; }
    .score-box {
        background: #ffffff;
        border: 1px solid #d9e2ec;
        border-radius: 12px;
        padding: 18px;
    }
    .score-total {
        font-size: 42px;
        font-weight: 800;
        color: #102a43;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def api_get(path: str):
    response = requests.get(f"{API_URL}{path}", timeout=6)
    response.raise_for_status()
    return response.json()


def api_post(path: str, payload: dict):
    response = requests.post(f"{API_URL}{path}", json=payload, timeout=6)
    response.raise_for_status()
    return response.json()


def api_patch(path: str, payload: dict):
    response = requests.patch(f"{API_URL}{path}", json=payload, timeout=6)
    response.raise_for_status()
    return response.json()


def role_selector():
    st.markdown('<div class="cg-title">CAREGRID</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="cg-sub">ICU Prioritization & Decision Support</div>',
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown("### Select your role")

    role = st.selectbox(
        "Role",
        ["Doctor", "Nurse", "Coordinator", "Administrator"],
    )

    name_map = {
        "Doctor": "Dr. S. Krishnan",
        "Nurse": "Nurse on Duty",
        "Coordinator": "ICU Coordinator",
        "Administrator": "Hospital Administrator",
    }

    if st.button("ENTER CAREGRID", type="primary", width="stretch"):
        st.session_state.role = role
        st.session_state.user = name_map[role]
        st.rerun()

    st.info("Doctor has decision access. Other roles are view-only in the MVP.")


def registration_form(user: str):
    """Doctor-only patient registration + full clinical assessment."""
    with st.expander("➕ Register New Patient (Doctor only)", expanded=False):
        st.caption(
            "Prototype hackathon decision-support model. Scoring thresholds are "
            "not clinical guidelines. The doctor remains the final decision-maker."
        )
        with st.form("register_patient", clear_on_submit=False):
            r1, r2, r3 = st.columns(3)
            with r1:
                pid = st.text_input("Patient ID *", key="reg_id")
                age = st.number_input("Age", min_value=0, max_value=130, value=60, step=1, key="reg_age")
            with r2:
                pname = st.text_input("Patient name *", key="reg_name")
                waiting = st.number_input(
                    "Waiting time (minutes)", min_value=0, value=0, step=5, key="reg_wait"
                )
            with r3:
                diagnosis = st.text_input("Diagnosis *", key="reg_diag")
                critical = st.checkbox("Critical status", key="reg_crit")

            st.markdown("**Clinical assessment (prototype scoring factors)**")
            a1, a2, a3 = st.columns(3)
            with a1:
                severity = st.number_input(
                    "Clinical urgency / severity (0-35)",
                    min_value=0.0, max_value=35.0, value=0.0, step=1.0, key="reg_sev",
                )
                dependency = st.number_input(
                    "Critical-care dependency (0-25)",
                    min_value=0.0, max_value=25.0, value=0.0, step=1.0, key="reg_dep",
                )
            with a2:
                deterioration = st.number_input(
                    "Deterioration risk (0-20)",
                    min_value=0.0, max_value=20.0, value=0.0, step=1.0, key="reg_det",
                )
                resource = st.number_input(
                    "Resource compatibility (0-10)",
                    min_value=0.0, max_value=10.0, value=0.0, step=1.0, key="reg_res",
                )
            with a3:
                survival = st.number_input(
                    "Survival likelihood (0-100 %)",
                    min_value=0.0, max_value=100.0, value=0.0, step=1.0, key="reg_surv",
                )
            required_resources = st.text_input(
                "Required resources (comma separated)", key="reg_reqres"
            )

            submitted = st.form_submit_button("REGISTER PATIENT", type="primary")

        if submitted:
            errors = []
            if not pid.strip():
                errors.append("Patient ID is required.")
            if not pname.strip():
                errors.append("Patient name is required.")
            if not diagnosis.strip():
                errors.append("Diagnosis is required.")

            if errors:
                for e in errors:
                    st.error(e)
                return

            missing = []
            if severity <= 0:
                missing.append("Clinical urgency (severity)")
            if dependency <= 0:
                missing.append("Critical-care dependency")
            if deterioration <= 0:
                missing.append("Deterioration risk")
            if survival <= 0:
                missing.append("Survival likelihood")
            if resource <= 0:
                missing.append("Resource compatibility")
            if not required_resources.strip():
                missing.append("Required resources")
            if missing:
                st.warning(
                    "DATA INCOMPLETE — priority recommendation may be unreliable.\n\n"
                    "Missing: " + ", ".join(missing)
                )

            try:
                result = api_post(
                    "/patients",
                    {
                        "id": pid.strip(),
                        "name": pname.strip(),
                        "age": int(age),
                        "diagnosis": diagnosis.strip(),
                        "waiting_minutes": int(waiting),
                        "critical": bool(critical),
                        "severity": float(severity),
                        "dependency": float(dependency),
                        "deterioration": float(deterioration),
                        "survival_likelihood": float(survival),
                        "resource": float(resource),
                        "required_resources": required_resources.strip(),
                        "decision_maker": user,
                    },
                )
                st.success(
                    f"Patient {result['patient_id']} registered · "
                    f"priority score {result.get('priority_score')}/100. Queue re-ranked."
                )
                st.rerun()
            except requests.HTTPError as exc:
                st.error(f"Unable to register patient: {exc.response.text}")


def assessment_update(selected: dict, user: str):
    """Doctor-only clinical deterioration / assessment update."""
    with st.expander("✏️ Update Clinical Assessment (Doctor only)", expanded=False):
        st.caption(
            "Update while the patient waits. Changing any factor recalculates the "
            "priority score, re-ranks the queue and records old/new values in the audit trail."
        )
        with st.form(f"update_{selected['id']}"):
            u1, u2, u3 = st.columns(3)
            with u1:
                severity = st.number_input(
                    "Clinical urgency / severity (0-35)",
                    min_value=0.0, max_value=35.0,
                    value=float(selected.get("severity", 0)), step=1.0,
                )
                dependency = st.number_input(
                    "Critical-care dependency (0-25)",
                    min_value=0.0, max_value=25.0,
                    value=float(selected.get("dependency", 0)), step=1.0,
                )
            with u2:
                deterioration = st.number_input(
                    "Deterioration risk (0-20)",
                    min_value=0.0, max_value=20.0,
                    value=float(selected.get("deterioration", 0)), step=1.0,
                )
                resource = st.number_input(
                    "Resource compatibility (0-10)",
                    min_value=0.0, max_value=10.0,
                    value=float(selected.get("resource", 0)), step=1.0,
                )
            with u3:
                survival = st.number_input(
                    "Survival likelihood (0-100 %)",
                    min_value=0.0, max_value=100.0,
                    value=float(selected.get("survival_likelihood", 0)), step=1.0,
                )
                waiting = st.number_input(
                    "Waiting time (minutes)",
                    min_value=0, value=int(selected.get("waiting_minutes", 0)), step=5,
                )
            critical = st.checkbox(
                "Critical status", value=bool(selected.get("critical", 0))
            )
            required_resources = st.text_input(
                "Required resources", value=selected.get("required_resources", "") or ""
            )
            saved = st.form_submit_button("SAVE ASSESSMENT & RE-RANK", type="primary")

        if saved:
            try:
                result = api_patch(
                    f"/patients/{selected['id']}",
                    {
                        "severity": float(severity),
                        "dependency": float(dependency),
                        "deterioration": float(deterioration),
                        "resource": float(resource),
                        "survival_likelihood": float(survival),
                        "waiting_minutes": int(waiting),
                        "critical": bool(critical),
                        "required_resources": required_resources.strip(),
                        "decision_maker": user,
                    },
                )
                if result.get("changed_factors"):
                    st.success(
                        f"Assessment updated. Score {result['previous_score']} → "
                        f"{result['new_score']} · Rank #{result['previous_rank']} → "
                        f"#{result['new_rank']}."
                    )
                else:
                    st.info("No changes detected.")
                st.rerun()
            except requests.HTTPError as exc:
                st.error(f"Unable to update assessment: {exc.response.text}")


def what_changed(patient_id: str):
    """Reconstruct the most recent change set for a patient from the audit trail."""
    try:
        logs = api_get(f"/audit-log?patient_id={patient_id}")
    except requests.RequestException:
        return

    assessment_rows = [r for r in logs if r.get("event") == "ASSESSMENT_UPDATED"]
    if not assessment_rows:
        return

    latest_ts = assessment_rows[0]["event_time"]
    group = [r for r in logs if r.get("event_time") == latest_ts]

    factors = [
        r for r in group if r.get("event") == "ASSESSMENT_UPDATED"
    ]
    score_row = next((r for r in group if r.get("event") == "SCORE_CALCULATED"), None)
    rank_row = next(
        (r for r in group if r.get("event") == "RANK_CHANGED" and r.get("patient_id") == patient_id),
        None,
    )
    updated_by = factors[0].get("decision_maker") if factors else "—"

    st.markdown("#### What Changed?")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**PREVIOUS**")
        st.write(f"Score: {score_row['previous_value'] if score_row else '—'}")
        st.write(f"Rank: {rank_row['previous_value'] if rank_row else '—'}")
    with c2:
        st.markdown("**CURRENT**")
        st.write(f"Score: {score_row['new_value'] if score_row else '—'}")
        st.write(f"Rank: {rank_row['new_value'] if rank_row else '—'}")

    st.markdown("**Changed factors**")
    for f in factors:
        st.write(f"- {f.get('previous_value')}  →  {f.get('new_value')}")
    st.caption(f"Updated by: {updated_by} · Time: {latest_ts}")


def audit_trail_section():
    st.subheader("Recent Audit Activity")
    try:
        logs = api_get("/audit-log")
    except requests.RequestException:
        st.warning("Unable to load audit trail.")
        return

    if not logs:
        st.info("No audit events recorded yet.")
        return

    df = pd.DataFrame(logs)
    display_cols = [
        "event_time", "patient_id", "patient_name", "diagnosis", "event",
        "previous_value", "new_value", "recommendation", "decision",
        "decision_maker", "reason",
    ]
    for col in display_cols:
        if col not in df.columns:
            df[col] = None

    df = df[display_cols].rename(
        columns={
            "event_time": "Time",
            "patient_id": "Patient",
            "patient_name": "Name",
            "diagnosis": "Diagnosis",
            "event": "Event",
            "previous_value": "Previous",
            "new_value": "New",
            "recommendation": "Recommendation",
            "decision": "Decision",
            "decision_maker": "By",
            "reason": "Reason",
        }
    )
    st.dataframe(df, width="stretch", hide_index=True)


def bed_match_section(patient_id: str):
    """Show compatible ICU / step-down beds for the selected patient.

    Supporting suggestion only — CareGrid never auto-assigns beds.
    """
    st.markdown("#### Bed Match")
    try:
        match = api_get(f"/beds/match/{patient_id}")
    except requests.RequestException:
        st.warning("Unable to load bed match.")
        return

    st.caption(
        "System suggestion only — CareGrid does NOT automatically assign beds. "
        "The doctor makes the final decision."
    )
    if not match["required_resources"]:
        st.warning(
            "No required resources specified for this patient — bed match "
            "cannot be assessed reliably. Complete the patient's data."
        )
    else:
        st.write("**Required resources:** " + ", ".join(match["required_resources"]))

    badge = {"HIGH": "🟢 HIGH", "MEDIUM": "🟡 MEDIUM", "LOW": "🔴 LOW"}

    for title, key in [("ICU Beds", "icu_matches"), ("Step-Down Beds", "step_down_matches")]:
        st.markdown(f"**{title}**")
        beds = match[key]
        if not beds:
            st.info(f"No available {title.lower()} at the moment.")
            continue
        for b in beds:
            with st.container(border=True):
                st.markdown(
                    f"**{b['bed_id']}** · {str(b['status']).upper()} · "
                    f"MATCH: {badge[b['match_level']]}"
                )
                mc1, mc2 = st.columns(2)
                with mc1:
                    if b["matched"]:
                        st.success("✓ " + ", ".join(b["matched"]))
                    else:
                        st.write("No required resource matched")
                with mc2:
                    if b["missing"]:
                        st.error("✗ Missing: " + ", ".join(b["missing"]))
                    elif match["required_resources"]:
                        st.success("All required resources present")
                st.caption(
                    f"Equipment: {', '.join(b['equipment']) or '—'} · "
                    f"Isolation: {b['isolation'] or '—'} · "
                    f"Compatibility: {b['compatibility'] or '—'}"
                )
                st.caption(b["reason"])


def dashboard():
    role = st.session_state.role
    user = st.session_state.user
    is_doctor = role == "Doctor"

    st.markdown('<div class="cg-title">CAREGRID</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="cg-sub">ICU Prioritization & Decision Support</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        st.markdown(
            f"**{user}** · {'Modify access' if is_doctor else 'View-only'}"
        )
    with c2:
        st.success("SYSTEM ACTIVE")
    with c3:
        if st.button("Switch Role"):
            st.session_state.clear()
            st.rerun()

    try:
        patients = api_get("/patients")
        beds = api_get("/icu/beds")
        step_down = api_get("/step-down/beds")
    except requests.RequestException:
        st.error("CareGrid API is not running. Start FastAPI in another terminal.")
        return

    waiting = len(patients)
    critical = sum(int(p["critical"]) for p in patients)
    available = sum(b["status"] == "available" for b in beds)
    occupied = sum(b["status"] == "occupied" for b in beds)
    cleaning = sum(b["status"] == "cleaning" for b in beds)
    sd_available = sum(b["status"] == "available" for b in step_down)
    alerts_count = (
        critical
        + sum(1 for p in patients if p["waiting_minutes"] >= 240)
        + sum(1 for p in patients if not p.get("data_complete", True))
    )

    st.markdown("**ICU Capacity**")
    cols = st.columns(6)
    cols[0].metric("ICU AVAILABLE", available)
    cols[1].metric("ICU OCCUPIED", occupied)
    cols[2].metric("ICU PREPARING", cleaning)
    cols[3].metric("STEP-DOWN AVAIL.", sd_available)
    cols[4].metric("WAITING", waiting)
    cols[5].metric("CRITICAL", critical)
    if alerts_count:
        st.warning(f"⚠️ {alerts_count} active alert(s) — see Alerts panel below.")
    else:
        st.success("No active alerts.")

    st.divider()

    # Doctor-only patient registration.
    if is_doctor:
        registration_form(user)
    else:
        st.info(
            "View-only mode. Patient registration and clinical assessment "
            "are restricted to authorized doctors."
        )

    st.divider()

    left, right = st.columns([1.5, 1])

    with left:
        st.subheader("Priority Queue")

        if patients:
            df = pd.DataFrame(patients)
            df.insert(0, "Rank", [p["rank"] for p in patients])

            queue_df = df[
                [
                    "Rank",
                    "id",
                    "name",
                    "diagnosis",
                    "priority_score",
                    "waiting_minutes",
                    "survival_likelihood",
                ]
            ].rename(
                columns={
                    "id": "Patient",
                    "name": "Name",
                    "diagnosis": "Diagnosis",
                    "priority_score": "Priority",
                    "waiting_minutes": "Waiting (min)",
                    "survival_likelihood": "Survival %*",
                }
            )

            st.dataframe(
                queue_df,
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("No patients currently waiting.")

    with right:
        st.subheader("Alerts")

        for p in patients:
            if p["critical"]:
                st.error(
                    f"{p['id']} — very high risk · {p['diagnosis']}"
                )

        for p in patients:
            if p["waiting_minutes"] >= 240:
                st.warning(
                    f"{p['id']} — waiting > 4 hours"
                )

        for p in patients:
            if not p.get("data_complete", True):
                st.warning(
                    f"{p['id']} — incomplete clinical data · recommendation may be unreliable"
                )

        for b in beds:
            if b["status"] == "available":
                st.success(
                    f"{b['id']} available · {b['compatibility']} compatibility"
                )

    st.divider()

    # Patient explanation
    st.subheader("Selected Patient")

    if patients:
        patient_ids = [p["id"] for p in patients]
        selected_id = st.selectbox(
            "Select a patient to inspect the recommendation",
            patient_ids,
        )
        selected = next(p for p in patients if p["id"] == selected_id)

        # Data-quality warning.
        missing = selected.get("missing_data", [])
        if missing:
            st.warning(
                "DATA INCOMPLETE — priority recommendation may be unreliable.\n\n"
                "Missing:\n- " + "\n- ".join(missing)
            )

        detail_left, detail_right = st.columns([1.2, 1])

        with detail_left:
            st.markdown(
                f"### {selected['id']} — {selected['diagnosis']}"
            )
            st.metric(
                "Priority Score",
                f"{selected['priority_score']}/100",
            )
            st.write(
                f"**System Recommendation:** {selected['recommendation']}"
            )
            care = selected.get("care_level", "—")
            care_badge = {
                "ICU Candidate": "🟥 ICU Candidate",
                "Step-Down Candidate": "🟧 Step-Down Candidate",
                "Ward / Continue Care": "🟩 Ward / Continue Care",
            }.get(care, care)
            st.markdown(f"**Care Level:** {care_badge}")
            st.caption(
                selected.get("care_level_note", "")
                or "Prototype disposition threshold — not a clinical guideline."
            )
            st.caption(
                "Prototype scoring framework. Final clinical decision remains with the authorized clinician."
            )

            score_df = pd.DataFrame(
                [
                    {
                        "Factor": "Clinical Urgency",
                        "Score": selected["scores"]["severity"],
                        "Max": 35,
                    },
                    {
                        "Factor": "Critical Dependency",
                        "Score": selected["scores"]["dependency"],
                        "Max": 25,
                    },
                    {
                        "Factor": "Deterioration Risk",
                        "Score": selected["scores"]["deterioration"],
                        "Max": 20,
                    },
                    {
                        "Factor": "Waiting Time",
                        "Score": selected["scores"]["waiting"],
                        "Max": 10,
                    },
                    {
                        "Factor": "Resource Compatibility",
                        "Score": selected["scores"]["resource"],
                        "Max": 10,
                    },
                ]
            )
            score_df["Contribution %"] = (
                score_df["Score"] / score_df["Max"] * 100
            ).round(0)

            chart = px.bar(
                score_df,
                x="Contribution %",
                y="Factor",
                orientation="h",
                range_x=[0, 100],
                text="Score",
                title="WHY THIS RANK?",
            )
            chart.update_layout(
                margin=dict(l=10, r=10, t=45, b=10),
                height=300,
            )
            st.plotly_chart(chart, width="stretch")

        with detail_right:
            st.markdown("### Patient Details")
            st.write(f"**Name:** {selected['name']}")
            st.write(f"**Age:** {selected.get('age', '—')}")
            st.write(f"**Diagnosis:** {selected['diagnosis']}")
            st.write(f"**Waiting:** {selected['waiting_minutes']} minutes")
            st.write(
                f"**Survival likelihood (prototype):** {selected['survival_likelihood']}%"
            )
            st.write(
                f"**Required resources:** {selected['required_resources'] or 'Not specified'}"
            )
            st.write(f"**Explanation:** {selected['score_explanation']}")

            if is_doctor:
                st.markdown("### Doctor Decision")
                action = st.radio(
                    "Decision",
                    ["ACCEPT", "OVERRIDE", "DEFER"],
                    horizontal=True,
                    key=f"decision_{selected['id']}",
                )

                reason = "—"
                if action in {"OVERRIDE", "DEFER"}:
                    reason = st.text_area(
                        f"{action.title()} reason",
                        key=f"reason_{selected['id']}",
                    )

                if st.button(
                    "SAVE DECISION",
                    type="primary",
                    key=f"save_{selected['id']}",
                ):
                    if action in {"OVERRIDE", "DEFER"} and not reason.strip():
                        st.error("Reason is mandatory for Override/Defer.")
                    else:
                        try:
                            result = api_post(
                                "/allocation",
                                {
                                    "patient_id": selected["id"],
                                    "decision": action,
                                    "decision_maker": user,
                                    "reason": reason,
                                    "recommendation": selected["recommendation"],
                                },
                            )
                            st.success(result["message"])
                            st.rerun()
                        except requests.HTTPError as exc:
                            st.error(
                                f"Unable to save decision: {exc.response.text}"
                            )
            else:
                st.info(
                    "View-only mode. Clinical decisions are restricted to authorized doctors."
                )

        # Doctor-only assessment update + What Changed (audit-driven).
        if is_doctor:
            assessment_update(selected, user)

        st.divider()
        bed_match_section(selected["id"])

        st.divider()
        what_changed(selected["id"])

    st.divider()

    st.subheader("ICU Bed Status")
    bed_df = pd.DataFrame(beds)

    if not bed_df.empty:
        st.dataframe(
            bed_df[
                [
                    "id",
                    "status",
                    "patient_id",
                    "patient_name",
                    "diagnosis",
                    "equipment",
                    "compatibility",
                ]
            ],
            width="stretch",
            hide_index=True,
        )

    st.subheader("Step-Down Bed Status")
    sd_df = pd.DataFrame(step_down)
    if not sd_df.empty:
        st.dataframe(
            sd_df[
                ["id", "status", "patient_id", "patient_name", "diagnosis", "equipment", "compatibility"]
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No step-down beds configured.")

    st.divider()
    audit_trail_section()

    st.caption(
        "CareGrid scoring is a hackathon prototype decision-support framework. "
        "Final clinical decisions remain with authorized clinicians."
    )


def analytics_page():
    role = st.session_state.role
    user = st.session_state.user
    is_doctor = role == "Doctor"

    st.markdown('<div class="cg-title">CAREGRID</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="cg-sub">Analytics / ML Validation</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "This section uses TRAINING / VALIDATION data — separate from the "
        "LIVE / DEMO clinical dashboard. The ML model provides a supporting "
        "survival estimate only; it never assigns beds or makes clinical decisions."
    )

    try:
        status = api_get("/datasets/status")
        ml = api_get("/ml/status")
    except requests.RequestException:
        st.error("CareGrid API is not running.")
        return

    st.subheader("Dataset Status")
    st.caption(
        "LIVE / DEMO clinical data is separate from these TRAINING / OPERATIONAL "
        "datasets. Operational datasets are real uploads; the survival model uses "
        "clearly-labelled SYNTHETIC data (no real ICU-outcome dataset was provided)."
    )
    operational = {k: v for k, v in status.items() if v.get("category") == "operational"}
    ml_syn = {k: v for k, v in status.items() if v.get("category") == "ml_synthetic"}

    st.markdown("**Operational datasets (real uploads)**")
    op_items = list(operational.items())
    for i in range(0, len(op_items), 2):
        row = op_items[i:i + 2]
        cols = st.columns(2)
        for col, (key, info) in zip(cols, row):
            with col:
                st.markdown(f"**{info['title']}**")
                if info["loaded"]:
                    st.success(f"Loaded · {info['rows']} rows")
                    st.caption(f"Source: {info['source']}")
                    st.caption(f"File: {info['filename']} · Imported: {info['imported_at']}")
                else:
                    st.error("Not Loaded")
                    st.caption(f"Expected file in data/raw/: {info['filename']}")
                if is_doctor:
                    if st.button(f"Import {key}", key=f"imp_{key}"):
                        try:
                            res = api_post(f"/datasets/{key}/import", {})
                            if res.get("status") == "ok":
                                st.success(
                                    f"Imported {res['imported_rows']} rows "
                                    f"({res['rejected_rows']} rejected)."
                                )
                            else:
                                st.warning(res.get("message", "Import did not run."))
                            st.rerun()
                        except requests.HTTPError as exc:
                            st.error(f"Import failed: {exc.response.text}")

    st.divider()

    st.subheader("Survival Model")
    for key, info in ml_syn.items():
        tag = "Loaded" if info["loaded"] else "Not Loaded"
        st.caption(
            f"Training data: {info['title']} — {tag} ({info['rows']} rows) · "
            f"{info['source']}"
        )
    mcol1, mcol2 = st.columns([1, 2])
    with mcol1:
        if ml.get("trained"):
            st.success("Trained")
            st.write(f"**Model version:** {ml.get('model_version')}")
            st.write(f"**Model:** {ml.get('model_name')}")
            st.caption(f"Trained at: {ml.get('trained_at')}")
        else:
            st.error("Not Trained")
            st.caption("Import the ICU outcome dataset, then train.")

    with mcol2:
        if is_doctor:
            if st.button("Train survival model", type="primary"):
                with st.spinner("Training on imported ICU outcome dataset..."):
                    try:
                        res = api_post("/ml/train", {"trained_by": user})
                        if res.get("status") == "ok":
                            st.success(
                                f"Model {res['model_version']} trained on "
                                f"{res['n_records']} records "
                                f"(target: {res['target_column']})."
                            )
                        else:
                            st.warning(res.get("message", "Training failed."))
                            if res.get("candidate_targets"):
                                st.caption(
                                    "Candidate target columns: "
                                    + ", ".join(res["candidate_targets"])
                                )
                        st.rerun()
                    except requests.HTTPError as exc:
                        st.error(f"Training failed: {exc.response.text}")
        else:
            st.info("View-only. Model training is restricted to authorized doctors.")

    # Validation metrics
    try:
        val = api_get("/ml/validation")
    except requests.RequestException:
        val = {"trained": False}

    if val.get("trained"):
        st.divider()
        st.subheader("Validation Metrics")
        st.caption(
            f"Dataset source: {val.get('dataset_source')} · "
            f"Target: {val.get('target_column')} ({val.get('target_meaning')})"
        )

        m = val.get("metrics", {})
        info_cols = st.columns(4)
        info_cols[0].metric("Records", val.get("n_records"))
        info_cols[1].metric("Train / Test", f"{val.get('n_train')} / {val.get('n_test')}")
        info_cols[2].metric("ROC-AUC", m.get("roc_auc"))
        info_cols[3].metric("Accuracy", m.get("accuracy"))

        met_cols = st.columns(3)
        met_cols[0].metric("Precision", m.get("precision"))
        met_cols[1].metric("Recall / Sensitivity", m.get("recall_sensitivity"))
        met_cols[2].metric("Specificity", m.get("specificity"))

        st.write("**Features used:** " + ", ".join(val.get("features", [])))

        g1, g2 = st.columns(2)
        with g1:
            cm = m.get("confusion_matrix", {})
            cm_df = pd.DataFrame(
                [[cm.get("tn", 0), cm.get("fp", 0)], [cm.get("fn", 0), cm.get("tp", 0)]],
                index=["Actual 0", "Actual 1"],
                columns=["Pred 0", "Pred 1"],
            )
            fig_cm = px.imshow(
                cm_df,
                text_auto=True,
                color_continuous_scale="Blues",
                title="Confusion Matrix",
            )
            fig_cm.update_layout(height=320, margin=dict(l=10, r=10, t=45, b=10))
            st.plotly_chart(fig_cm, width="stretch")

        with g2:
            roc = m.get("roc_curve", {})
            if roc.get("fpr"):
                roc_df = pd.DataFrame({"FPR": roc["fpr"], "TPR": roc["tpr"]})
                fig_roc = px.line(
                    roc_df, x="FPR", y="TPR",
                    title=f"ROC Curve (AUC = {m.get('roc_auc')})",
                )
                fig_roc.add_shape(
                    type="line", x0=0, y0=0, x1=1, y1=1,
                    line=dict(dash="dash", color="gray"),
                )
                fig_roc.update_layout(height=320, margin=dict(l=10, r=10, t=45, b=10))
                st.plotly_chart(fig_roc, width="stretch")

        imp = m.get("feature_importance", [])
        if imp:
            imp_df = pd.DataFrame(imp)
            imp_df["abs_weight"] = imp_df["weight"].abs()
            imp_df = imp_df.sort_values("abs_weight")
            fig_imp = px.bar(
                imp_df, x="weight", y="feature", orientation="h",
                title="Feature Importance (logistic regression weights)",
            )
            fig_imp.update_layout(height=360, margin=dict(l=10, r=10, t=45, b=10))
            st.plotly_chart(fig_imp, width="stretch")

    st.caption(
        "Metrics are computed from a held-out test split of the imported dataset. "
        "No metrics are fabricated. SYNTHETIC demo data is clearly labelled as such."
    )


def _csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def capacity_page():
    st.markdown('<div class="cg-title">CAREGRID</div>', unsafe_allow_html=True)
    st.markdown('<div class="cg-sub">Capacity Analytics</div>', unsafe_allow_html=True)
    try:
        cap = api_get("/analytics/capacity")
    except requests.RequestException:
        st.error("CareGrid API is not running.")
        return

    st.subheader("Live Bed Capacity")
    c = st.columns(6)
    c[0].metric("ICU Available", cap["icu"]["available"])
    c[1].metric("ICU Occupied", cap["icu"]["occupied"])
    c[2].metric("ICU Preparing", cap["icu"]["preparing"])
    c[3].metric("Step-Down Available", cap["step_down"]["available"])
    c[4].metric("Step-Down Occupied", cap["step_down"]["occupied"])
    c[5].metric("Step-Down Preparing", cap["step_down"]["preparing"])

    st.divider()
    st.subheader("Weekly Demand & Refusal by Service")
    st.caption("Aggregated from the real operational dataset (services_weekly). Not simulated.")
    if not cap["loaded"]:
        st.info("Operational capacity dataset not loaded. Import it in Analytics / Validation.")
        return
    df = pd.DataFrame(cap["services"])
    st.dataframe(df, width="stretch", hide_index=True)
    fig = px.bar(
        df, x="service", y=["total_admitted", "total_refused"],
        barmode="group", title="Admitted vs Refused by Service (52 weeks)",
    )
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=45, b=10))
    st.plotly_chart(fig, width="stretch")
    st.session_state["_capacity_df"] = df


def export_page():
    st.markdown('<div class="cg-title">CAREGRID</div>', unsafe_allow_html=True)
    st.markdown('<div class="cg-sub">CSV Export</div>', unsafe_allow_html=True)
    st.caption("Download current CareGrid data as CSV. Read-only — available to all roles.")

    try:
        patients = api_get("/patients")
        beds = api_get("/icu/beds")
        step_down = api_get("/step-down/beds")
        audit = api_get("/audit-log")
        cap = api_get("/analytics/capacity")
    except requests.RequestException:
        st.error("CareGrid API is not running.")
        return

    # Priority queue (full)
    if patients:
        q = pd.DataFrame(patients)
        queue_df = q[["rank", "id", "name", "diagnosis", "priority_score",
                      "waiting_minutes", "survival_likelihood", "care_level"]]
        ranking_summary = q[["rank", "id", "name", "priority_score", "care_level", "recommendation"]]
    else:
        queue_df = pd.DataFrame()
        ranking_summary = pd.DataFrame()

    exports = [
        ("Current Priority Queue", "priority_queue.csv", queue_df),
        ("Patient Ranking Summary", "ranking_summary.csv", ranking_summary),
        ("Audit Trail", "audit_trail.csv", pd.DataFrame(audit)),
        ("ICU Bed Status", "icu_bed_status.csv", pd.DataFrame(beds)),
        ("Step-Down Bed Status", "step_down_bed_status.csv", pd.DataFrame(step_down)),
        ("Capacity Analytics Summary", "capacity_summary.csv", pd.DataFrame(cap.get("services", []))),
    ]

    for label, fname, df in exports:
        cols = st.columns([2, 1])
        cols[0].markdown(f"**{label}** · {len(df)} rows")
        if df.empty:
            cols[1].caption("No data")
        else:
            cols[1].download_button(
                "Download CSV", data=_csv_bytes(df), file_name=fname,
                mime="text/csv", key=f"dl_{fname}",
            )


# ---- Simulation Mode (synthetic demo patients only; never touches live DB) ----
SIM_PATIENTS = [
    {"id": "SIM-1", "name": "Sim A", "diagnosis": "Septic shock",
     "scores": {"severity": 30, "dependency": 22, "deterioration": 12, "waiting": 3, "resource": 7},
     "survival": 78, "waiting_minutes": 90},
    {"id": "SIM-2", "name": "Sim B", "diagnosis": "Respiratory failure",
     "scores": {"severity": 24, "dependency": 18, "deterioration": 10, "waiting": 6, "resource": 8},
     "survival": 82, "waiting_minutes": 180},
    {"id": "SIM-3", "name": "Sim C", "diagnosis": "Post-op monitoring",
     "scores": {"severity": 16, "dependency": 12, "deterioration": 8, "waiting": 8, "resource": 9},
     "survival": 90, "waiting_minutes": 240},
]


def _sim_rank(patients):
    ranked = []
    for p in patients:
        try:
            score = api_post("/priority/calculate", p["scores"])["score"]
        except requests.RequestException:
            score = sum(p["scores"].values())
        ranked.append({**p, "score": score})
    ranked.sort(key=lambda r: (-r["score"], -r["survival"]))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return ranked


def simulation_page():
    st.markdown('<div class="cg-title">CAREGRID</div>', unsafe_allow_html=True)
    st.markdown('<div class="cg-sub">Simulation Mode (demo)</div>', unsafe_allow_html=True)
    st.caption(
        "Synthetic demo scenario only — does NOT use real datasets or touch the live "
        "patient database. Demonstrates: Patient → Score → Rank → Bed change → Re-rank "
        "→ Doctor decision → Audit."
    )

    cols = st.columns(3)
    start = cols[0].button("▶ Start Simulation", type="primary")
    step = cols[1].button("⏭ Next Event")
    reset = cols[2].button("⟲ Reset Simulation")

    if reset:
        st.session_state.pop("sim", None)

    if start or "sim" not in st.session_state:
        st.session_state["sim"] = {
            "patients": [dict(p, scores=dict(p["scores"])) for p in SIM_PATIENTS[:2]],
            "beds_available": 0,
            "timeline": ["Simulation started with 2 waiting patients."],
            "step": 0,
        }

    sim = st.session_state["sim"]

    if step:
        s = sim["step"]
        if s == 0:
            sim["patients"].append(dict(SIM_PATIENTS[2], scores=dict(SIM_PATIENTS[2]["scores"])))
            sim["timeline"].append("New patient SIM-3 arrived and was scored + queued.")
        elif s == 1:
            for p in sim["patients"]:
                if p["id"] == "SIM-2":
                    p["scores"]["deterioration"] = 20
                    p["survival"] = 60
            sim["timeline"].append("Deterioration event: SIM-2 deterioration risk ↑ → re-ranked.")
        elif s == 2:
            sim["beds_available"] = 1
            sim["timeline"].append("Bed became available: ICU-07 now free.")
        elif s == 3:
            ranked = _sim_rank(sim["patients"])
            top = ranked[0]
            sim["timeline"].append(
                f"Doctor ACCEPTED {top['id']} (Priority #1) → audit event recorded (simulated)."
            )
        else:
            sim["timeline"].append("Scenario complete. Reset to run again.")
        sim["step"] = min(s + 1, 4)

    st.subheader("Live Queue (simulated)")
    ranked = _sim_rank(sim["patients"])
    st.dataframe(
        pd.DataFrame(
            [{"Rank": r["rank"], "Patient": r["id"], "Diagnosis": r["diagnosis"],
              "Score": r["score"], "Survival %": r["survival"],
              "Waiting (min)": r["waiting_minutes"]} for r in ranked]
        ),
        width="stretch", hide_index=True,
    )
    st.caption(f"ICU beds available (simulated): {sim['beds_available']}")

    st.subheader("Event Timeline")
    for i, ev in enumerate(sim["timeline"], 1):
        st.markdown(f"**{i}.** {ev}")


if "role" not in st.session_state:
    role_selector()
else:
    st.sidebar.markdown("### CareGrid")
    view = st.sidebar.radio(
        "View",
        [
            "Clinical Dashboard",
            "Analytics / Validation",
            "Capacity Analytics",
            "Simulation",
            "CSV Export",
        ],
    )
    st.sidebar.caption(f"{st.session_state.get('user', '')} · {st.session_state.get('role', '')}")
    st.sidebar.caption("Prototype decision support — clinician remains final decision-maker.")
    if view == "Clinical Dashboard":
        dashboard()
    elif view == "Analytics / Validation":
        analytics_page()
    elif view == "Capacity Analytics":
        capacity_page()
    elif view == "Simulation":
        simulation_page()
    else:
        export_page()
