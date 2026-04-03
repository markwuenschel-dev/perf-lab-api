"""Encode prescriber output as a constraint candidate dict."""

from __future__ import annotations

from typing import Any

from app.schemas.prescription import WorkoutPrescription
from app.schemas.session_draft import SessionDraft
from app.schemas.training_goals import TrainingGoal


def _infer_tags(rx: WorkoutPrescription, goal: TrainingGoal, draft: SessionDraft | None) -> list[str]:
    tags: list[str] = []
    t = f"{rx.type} {rx.focus}".lower()
    if draft and draft.intensity_band in ("high", "max"):
        tags.append("high_intensity_session")
    if "recovery" in t or "deload" in t or "rest" in t:
        tags.append("recovery")
    if goal == "OlympicLifts":
        tags.append("olympic")
        if "technique" in t or "drill" in t or "snatch" in t or "clean" in t:
            tags.append("technical_olift")
        if "rpe 8" in t or "heavy" in t or "single" in t:
            tags.append("heavy_technical")
    if goal in ("Running", "HalfMarathon", "FullMarathon", "Sprinting"):
        tags.append("running")
        if "tempo" in t or "threshold" in t or "interval" in t or "vo2" in t:
            tags.append("threshold_or_vo2")
            tags.append("vo2_candidate")
        if "long" in t or "distance" in t or "marathon" in t:
            tags.append("long_run")
        if "zone 2" in t or "easy" in t or "aerobic base" in t:
            tags.append("easy_run")
    if goal == "Powerlifting":
        tags.append("powerlifting")
        if "deadlift" in t:
            tags.append("deadlift_session")
        if "squat" in t:
            tags.append("squat_session")
    if goal == "Hypertrophy":
        tags.append("hypertrophy_block")
    if goal in ("Gymnastics", "Calisthenics", "Grip"):
        if "kipping" in t:
            tags.append("kipping_skill")
        tags.append("gymnastics_family")
    if goal == "MetCon" or "metabolic" in t or "interval" in t:
        tags.append("metcon")
    return list(dict.fromkeys(tags))


def _main_lift(goal: TrainingGoal, rx: WorkoutPrescription) -> str:
    t = f"{rx.type} {rx.focus}".lower()
    if "deadlift" in t:
        return "deadlift"
    if "bench" in t:
        return "bench"
    if "squat" in t:
        return "squat"
    if goal in ("Running", "HalfMarathon", "FullMarathon"):
        if "long" in t:
            return "long_run"
        return "easy_run"
    if goal == "OlympicLifts":
        return "snatch_cj"
    if goal == "Sprinting":
        return "sprint"
    return "general"


def encode_session_candidate(
    rx: WorkoutPrescription,
    goal: TrainingGoal,
    branch_id: str,
    draft: SessionDraft | None = None,
) -> dict[str, Any]:
    """Stable shape for constraint + scoring (v1: single branch output)."""

    intensity_bucket = "moderate"
    if draft:
        intensity_bucket = draft.intensity_band
    else:
        low = f"{rx.type} {rx.focus}".lower()
        if "recovery" in low or "zone 2" in low or "easy" in low:
            intensity_bucket = "low"
        elif "max" in low or "rpe 8" in low or "sprint" in low or "near failure" in low:
            intensity_bucket = "high"

    d = draft
    tech = d.technical_emphasis if d else 0.55
    meta = d.metabolic_emphasis if d else 0.45
    neural = d.neural_emphasis if d else 0.5
    vol = d.volume_load_proxy if d else min(1.0, max(0.2, rx.duration_min / 90.0))
    max_reps = d.max_reps_per_set_cap if d else None

    estimated_cns_cost = neural * 0.5 + (1.0 - tech * 0.2) * 0.3 + vol * 0.2
    tissue_cost = meta * 0.25 + vol * 0.35

    ex_families: list[str] = []
    if goal == "OlympicLifts":
        ex_families = ["snatch", "clean_and_jerk", "squat", "pull"]
    elif goal in ("Running", "HalfMarathon", "FullMarathon"):
        ex_families = ["zone2_run", "tempo_threshold_run"]
    elif goal == "Powerlifting":
        ex_families = ["competition_squat", "competition_bench", "competition_deadlift"]
    elif goal == "Hypertrophy":
        ex_families = ["hypertrophy_accessory", "competition_lifts"]
    elif goal in ("Gymnastics", "Calisthenics"):
        ex_families = ["strict_pullup_dip", "handstand_line_drills", "ring_support"]

    tags = _infer_tags(rx, goal, d)

    return {
        "branch_id": branch_id,
        "goal": goal,
        "tags": tags,
        "intensity_bucket": intensity_bucket,
        "main_lift": _main_lift(goal, rx),
        "estimated_cns_cost": round(estimated_cns_cost, 4),
        "tissue_cost": round(tissue_cost, 4),
        "exercise_families": ex_families,
        "duration_min": rx.duration_min,
        "max_reps_per_set": max_reps,
        "technical_emphasis": tech,
        "metabolic_emphasis": meta,
        "neural_emphasis": neural,
        "volume_load_proxy": vol,
    }
