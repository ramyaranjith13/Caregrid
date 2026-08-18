"""Bed compatibility matching for CareGrid V2.

For a selected patient, compares the patient's required resources against
available ICU and step-down beds (equipment, isolation, compatibility) and
returns a HIGH / MEDIUM / LOW match with explicit reasons and missing
resources.

This is a SUPPORTING suggestion only — CareGrid never auto-assigns a bed.
The doctor makes the final decision.
"""

from ..database import get_connection

_LEVEL_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _parse(value) -> set:
    return {x.strip().lower() for x in str(value or "").split(",") if x.strip()}


def _match(required: set, bed: dict, care_level: str) -> dict:
    equip = _parse(bed.get("equipment"))
    matched = sorted(required & equip)
    missing = sorted(required - equip)
    coverage = 1.0 if not required else len(matched) / len(required)
    comp = str(bed.get("compatibility") or "").strip().lower()
    isolation = str(bed.get("isolation") or "")

    if not required:
        level = "LOW"
        reason = "No required resources specified — match cannot be assessed reliably."
    elif not missing:
        if comp == "high":
            level, reason = "HIGH", "All required resources available; high bed compatibility."
        elif comp == "medium":
            level, reason = "MEDIUM", "All required resources available; medium bed compatibility."
        else:
            level, reason = "MEDIUM", "All required resources available; compatibility not rated high."
    elif coverage >= 0.5:
        level = "MEDIUM"
        reason = f"Partial match — missing: {', '.join(missing)}."
    else:
        level = "LOW"
        reason = f"Low match — missing essential resources: {', '.join(missing)}."

    return {
        "bed_id": bed["id"],
        "care_level": care_level,
        "status": bed.get("status"),
        "equipment": sorted(equip),
        "isolation": isolation,
        "compatibility": bed.get("compatibility"),
        "matched": matched,
        "missing": missing,
        "coverage": round(coverage, 2),
        "match_level": level,
        "reason": reason,
    }


def match_beds_for_patient(patient_id: str):
    conn = get_connection()
    patient = conn.execute(
        "SELECT * FROM patients WHERE id = ?", (patient_id,)
    ).fetchone()
    if patient is None:
        conn.close()
        return None
    patient = dict(patient)
    required = _parse(patient.get("required_resources"))

    icu = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM icu_beds WHERE status = 'available' ORDER BY id"
        ).fetchall()
    ]
    step_down = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM step_down_beds WHERE status = 'available' ORDER BY id"
        ).fetchall()
    ]
    conn.close()

    icu_matches = [_match(required, b, "ICU") for b in icu]
    sd_matches = [_match(required, b, "Step-Down") for b in step_down]

    def _sort_key(m):
        return (_LEVEL_ORDER[m["match_level"]], len(m["missing"]), m["bed_id"])

    icu_matches.sort(key=_sort_key)
    sd_matches.sort(key=_sort_key)

    all_matches = icu_matches + sd_matches
    # Data-safety: never surface a confident "best" bed when required
    # resources are missing (avoids a misleading recommendation).
    best = None
    if required and all_matches:
        best = min(all_matches, key=_sort_key)

    return {
        "patient_id": patient_id,
        "patient_name": patient.get("name"),
        "required_resources": sorted(required),
        "data_complete": len(required) > 0,
        "icu_matches": icu_matches,
        "step_down_matches": sd_matches,
        "best_match": best,
    }
