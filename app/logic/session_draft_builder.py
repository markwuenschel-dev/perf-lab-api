"""Build SessionDraft from prescription text + goal + template heuristics."""

from typing import cast

from app.schemas.prescription import WorkoutPrescription
from app.schemas.program_template import ProgramTemplate
from app.schemas.state import UnifiedStateVector
from app.schemas.session_draft import IntensityBand, SessionDraft
from app.schemas.training_goals import TrainingGoal


def _infer_intensity(rx: WorkoutPrescription) -> str:
    t = f"{rx.type} {rx.focus}".lower()
    if "recovery" in t or "rest" in t or "passive" in t:
        return "low"
    if "max" in t or "rpe 8" in t or "near failure" in t:
        return "high"
    if "sprint" in t or "speed" in t or "fly" in t:
        return "high"
    if "zone 2" in t or "easy" in t or "walking" in t:
        return "low"
    return "moderate"


def build_session_draft(
    rx: WorkoutPrescription,
    goal: TrainingGoal,
    _state: UnifiedStateVector,
    template: ProgramTemplate | None,
) -> SessionDraft:
    """Normalize prescription into a structured draft for constraint checks."""
    intensity = _infer_intensity(rx)
    band = cast(
        IntensityBand,
        intensity if intensity in ("low", "moderate", "high", "max") else "moderate",
    )

    kind = rx.type.replace(" ", "_").lower()[:48] or "general"

    tech = 0.5
    meta = 0.4
    neural = 0.5
    vol = min(1.0, max(0.2, rx.duration_min / 90.0))

    gl = goal
    if gl in ("OlympicLifts",):
        tech, meta, neural = 0.9, 0.35, 0.65
    elif gl in ("Sprinting", "Power"):
        neural, meta = 0.85, 0.4
    elif gl in ("Running", "HalfMarathon", "FullMarathon"):
        meta, tech = 0.55, 0.3
    elif gl == "MetCon":
        meta = 0.75
    elif gl in ("Gymnastics", "Calisthenics"):
        tech = 0.85
    elif gl == "Grip":
        neural, tech = 0.55, 0.4

    if "recovery" in rx.type.lower() or "deload" in rx.type.lower():
        tech, meta, neural, vol = 0.35, 0.25, 0.2, 0.3

    max_reps = None
    if gl == "OlympicLifts":
        max_reps = 5
    z2 = None
    if gl in ("Running", "HalfMarathon", "FullMarathon") and template:
        z2 = float(template.load_distribution.get("zone2_fraction", 0.75))

    return SessionDraft(
        session_kind=kind,
        primary_modality=_guess_modality(rx, goal),
        intensity_band=band,  # type: ignore[arg-type]
        technical_emphasis=tech,
        metabolic_emphasis=meta,
        neural_emphasis=neural,
        volume_load_proxy=vol,
        max_reps_per_set_cap=max_reps,
        zone2_fraction_target=z2,
    )


def _guess_modality(rx: WorkoutPrescription, goal: TrainingGoal) -> str:
    if goal in ("Running", "HalfMarathon", "FullMarathon", "Sprinting"):
        return "running"
    if goal == "MetCon":
        return "conditioning"
    if goal in ("OlympicLifts", "Powerlifting", "Strength", "Power"):
        return "barbell"
    if goal in ("Calisthenics", "Gymnastics"):
        return "bodyweight"
    if goal == "Grip":
        return "grip"
    return "mixed"


