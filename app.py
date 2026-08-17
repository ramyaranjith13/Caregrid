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
    response = requests.get(f"{API_URL}{path}", timeout=4)
    response.raise_for_status()
    return response.json()


def api_post(path: str, payload: dict):
    response = requests.post(f"{API_URL}{path}", json=payload, timeout=4)
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
    except requests.RequestException:
        st.error("CareGrid API is not running. Start FastAPI in another terminal.")
        return

    waiting = len(patients)
    critical = sum(int(p["critical"]) for p in patients)
    available = sum(b["status"] == "available" for b in beds)
    occupied = sum(b["status"] == "occupied" for b in beds)
    cleaning = sum(b["status"] == "cleaning" for b in beds)

    cols = st.columns(5)
    cols[0].metric("AVAILABLE", available)
    cols[1].metric("OCCUPIED", occupied)
    cols[2].metric("PREPARING", cleaning)
    cols[3].metric("WAITING", waiting)
    cols[4].metric("CRITICAL", critical)

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

    st.caption(
        "CareGrid scoring is a hackathon prototype decision-support framework. "
        "Final clinical decisions remain with authorized clinicians."
    )


if "role" not in st.session_state:
    role_selector()
else:
    dashboard()
