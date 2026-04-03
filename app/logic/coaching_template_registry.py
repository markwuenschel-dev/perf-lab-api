"""
Structured coaching templates (v2) + TrainingGoal → template_id mapping.

Slim `ProgramTemplate` rows in `program_templates.json` remain the source for
`build_session_draft` (e.g. zone2_fraction). Toggle structured validation/scoring
via `Settings.USE_STRUCTURED_COACHING_TEMPLATES`.
"""

import json
from functools import lru_cache
from pathlib import Path

from app.schemas.coaching_template import StructuredCoachingTemplate
from app.schemas.training_goals import TrainingGoal

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "coaching_templates"

# Default template per TrainingGoal (public-principle abstractions — not proprietary programs).
GOAL_TO_TEMPLATE_ID: dict[str, str] = {
    "OlympicLifts": "tmpl_olift_pendlay_style_v1",
    "Power": "tmpl_olift_pendlay_style_v1",
    "Running": "tmpl_run_hinshaw_style_v1",
    "HalfMarathon": "tmpl_run_hinshaw_style_v1",
    "FullMarathon": "tmpl_run_hinshaw_style_v1",
    "Sprinting": "tmpl_run_hinshaw_style_v1",
    "Powerlifting": "tmpl_pl_531_style_v1",
    "Strength": "tmpl_pl_531_style_v1",
    "Hypertrophy": "tmpl_pl_juggernaut_style_v1",
    "MetCon": "tmpl_pl_juggernaut_style_v1",
    "General": "tmpl_run_hinshaw_style_v1",
    "Calisthenics": "tmpl_gymnastics_progression_v1",
    "Gymnastics": "tmpl_gymnastics_progression_v1",
    "Grip": "tmpl_gymnastics_progression_v1",
}


@lru_cache
def load_structured_templates() -> tuple[StructuredCoachingTemplate, ...]:
    path = _DATA_DIR / "bundled.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(StructuredCoachingTemplate.model_validate(t) for t in raw)


def get_structured_template_by_id(template_id: str) -> StructuredCoachingTemplate | None:
    for t in load_structured_templates():
        if t.template_id == template_id:
            return t
    return None


def get_structured_template_for_goal(goal: TrainingGoal) -> StructuredCoachingTemplate:
    tid = GOAL_TO_TEMPLATE_ID.get(goal)
    if tid:
        t = get_structured_template_by_id(tid)
        if t:
            return t
    # Fallback: first running template for aerobic bias, else first in bundle
    for cand in load_structured_templates():
        if cand.template_id == "tmpl_run_hinshaw_style_v1":
            return cand
    return load_structured_templates()[0]
