from typing import Dict, Any, List
from functools import cmp_to_key

SCORE_MAX = {
    "severity": 35,
    "dependency": 25,
    "deterioration": 20,
    "waiting": 10,
    "resource": 10,
}

SCORE_LABELS = {
    "severity": "Clinical urgency",
    "dependency": "Critical-care dependency",
    "deterioration": "Deterioration risk",
    "waiting": "Waiting time",
    "resource": "Resource compatibility",
}

# Required clinical fields for a reliable prototype recommendation.
# Used only for data-quality warnings, never to fabricate values.
REQUIRED_CLINICAL = {
    "severity": "Clinical urgency (severity)",
    "dependency": "Critical-care dependency",
    "deterioration": "Deterioration risk",
    "survival_likelihood": "Survival likelihood",
    "resource": "Resource compatibility",
    "required_resources": "Required resources",
}


def _clamp(value: float, maximum: float) -> float:
    return max(0.0, min(float(value), maximum))


def waiting_component(waiting_minutes: int) -> float:
    # Prototype rule: 1 point per 30 minutes, capped at 10.
    return _clamp(waiting_minutes / 30.0, 10)


def calculate_priority(scores: Dict[str, float]) -> int:
    """Deterministic prototype score. Not clinically validated."""
    total = 0.0
    for key, maximum in SCORE_MAX.items():
        total += _clamp(scores.get(key, 0), maximum)
    return int(round(total))


def score_breakdown_from_patient(patient: Dict[str, Any]) -> Dict[str, float]:
    return {
        "severity": _clamp(patient.get("severity", 0), 35),
        "dependency": _clamp(patient.get("dependency", 0), 25),
        "deterioration": _clamp(patient.get("deterioration", 0), 20),
        "waiting": waiting_component(int(patient.get("waiting_minutes", 0))),
        "resource": _clamp(patient.get("resource", 0), 10),
    }


def explain_score(scores: Dict[str, float]) -> str:
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_key, top_value = ordered[0]
    return (
        f"Highest contribution: {SCORE_LABELS[top_key]} "
        f"({top_value:.0f}/{SCORE_MAX[top_key]})."
    )


def missing_clinical_fields(patient: Dict[str, Any]) -> List[str]:
    """Return human-readable labels of required clinical fields that are missing.

    A numeric field is considered missing when it is None or <= 0.
    'required_resources' is missing when blank. This drives data-quality
    warnings so CareGrid never presents a confident recommendation on
    incomplete data.
    """
    missing: List[str] = []
    for key, label in REQUIRED_CLINICAL.items():
        value = patient.get(key)
        if key == "required_resources":
            if not str(value or "").strip():
                missing.append(label)
        else:
            try:
                numeric = float(value) if value is not None else 0.0
            except (TypeError, ValueError):
                numeric = 0.0
            if numeric <= 0:
                missing.append(label)
    return missing


def _compare(a: Dict[str, Any], b: Dict[str, Any]) -> int:
    score_diff = a["priority_score"] - b["priority_score"]
    if abs(score_diff) > 2:
        return -1 if score_diff > 0 else 1

    # Near-tie rule: survival likelihood, then waiting time.
    if a["survival_likelihood"] != b["survival_likelihood"]:
        return -1 if a["survival_likelihood"] > b["survival_likelihood"] else 1

    if a["waiting_minutes"] != b["waiting_minutes"]:
        return -1 if a["waiting_minutes"] > b["waiting_minutes"] else 1

    if str(a["id"]) < str(b["id"]):
        return -1
    if str(a["id"]) > str(b["id"]):
        return 1
    return 0


def rank_patients(patients):
    enriched = []
    for patient in patients:
        row = dict(patient)
        scores = score_breakdown_from_patient(row)
        row["scores"] = scores
        row["priority_score"] = calculate_priority(scores)
        row["score_explanation"] = explain_score(scores)
        row["missing_data"] = missing_clinical_fields(row)
        row["data_complete"] = len(row["missing_data"]) == 0
        enriched.append(row)

    enriched.sort(key=cmp_to_key(_compare))

    for index, row in enumerate(enriched, start=1):
        row["rank"] = index
        row["recommendation"] = f"Priority #{index}"

    return enriched
