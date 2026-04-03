"""Load training primitives and program templates from packaged JSON."""

import json
from functools import lru_cache
from pathlib import Path

from app.schemas.program_template import ProgramTemplate
from app.schemas.provenance import TrainingPrimitive

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache
def load_training_primitives() -> tuple[TrainingPrimitive, ...]:
    path = _DATA_DIR / "training_primitives.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(TrainingPrimitive.model_validate(p) for p in raw)


def get_primitive_map() -> dict[str, TrainingPrimitive]:
    return {p.id: p for p in load_training_primitives()}


def primitive_names(ids: list[str]) -> list[str]:
    m = get_primitive_map()
    return [m[i].name for i in ids if i in m]


@lru_cache
def load_program_templates() -> tuple[ProgramTemplate, ...]:
    path = _DATA_DIR / "program_templates.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(ProgramTemplate.model_validate(t) for t in raw)


def get_template_for_goal(goal: str) -> ProgramTemplate | None:
    """First template whose goals list contains this TrainingGoal."""
    for t in load_program_templates():
        if goal in t.goals:
            return t
    return None


def get_fallback_template() -> ProgramTemplate:
    """Default GPP template."""
    for t in load_program_templates():
        if t.id == "gpp_general":
            return t
    return load_program_templates()[-1]
