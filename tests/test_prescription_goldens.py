"""A characterization snapshot of what every template prescribes today (phase 0 harness).

This file makes no claim that the current output is CORRECT. It claims only that it is what
the engine produces right now, so that the phases which restructure prescriptions — typed
session structure (2), workout families (4), the running/HYROX conversions (5-6) — can prove
they changed nothing they did not mean to change.

How to use it when a change is intentional: run with ``--golden-update``, read the diff
hunk by hunk, and include the before/after in the PR body. An unexplained diff is the bug
this file exists to catch.

Recorded per template: the branch id, session type, focus, duration, the declared scoring
axes, and the shape of the exercise slots (name pattern, sets, reps) — the fields that decide
what an athlete is actually asked to do. Deliberately NOT recorded: rationale prose, which is
wording rather than prescription.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.logic.candidate_library import GOAL_TEMPLATE_LIBRARY

GOLDEN_PATH = Path(__file__).parent / "data" / "prescription_goldens.json"


def _slot_shape(slot) -> dict:
    """The part of an exercise slot that decides the work, not the wording."""
    return {
        "sets": getattr(slot, "sets", None),
        "reps": getattr(slot, "reps", None),
        "load_note": getattr(slot, "load_note", None),
        "requirements": sorted(str(r) for r in getattr(slot, "requirements", ()) or ()),
    }


def _scoring_shape(template) -> dict | None:
    spec = getattr(template, "scoring", None)
    if spec is None:
        return None
    return {
        "fatigue_axis": getattr(spec, "fatigue_axis", None),
        "fatigue_weight": getattr(spec, "fatigue_weight", None),
        "tissue_axes": list(getattr(spec, "tissue_axes", ()) or ()),
        "tissue_weight": getattr(spec, "tissue_weight", None),
    }


def _corpus() -> dict[str, dict]:
    """Every template the engine can prescribe, keyed by domain and branch id."""
    corpus: dict[str, dict] = {}
    for domain in sorted(GOAL_TEMPLATE_LIBRARY):
        for template in GOAL_TEMPLATE_LIBRARY[domain]:
            key = f"{domain}/{template.branch_id}"
            slots = getattr(template, "exercise_slots", None) or []
            corpus[key] = {
                "type": template.type,
                "focus": template.focus,
                "duration_min": template.duration_min,
                "goal_alignment": getattr(template, "goal_alignment", None),
                "tags": sorted(getattr(template, "tags", ()) or ()),
                "slot_count": len(slots),
                "slots": [_slot_shape(s) for s in slots],
                "scoring": _scoring_shape(template),
            }
    return corpus


def test_the_prescription_corpus_matches_its_golden(request) -> None:
    """The safety net for phases 2-6. A diff here is either intended or a defect."""
    current = _corpus()

    if request.config.getoption("--golden-update", default=False):  # pragma: no cover
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", "utf-8")
        pytest.skip("golden corpus rewritten")

    assert GOLDEN_PATH.exists(), (
        f"golden corpus missing — create it with: pytest {Path(__file__).name} --golden-update"
    )
    expected = json.loads(GOLDEN_PATH.read_text("utf-8"))

    assert current == expected, (
        "prescribed work changed. If intended, rerun with --golden-update and put the "
        "before/after in the PR body."
    )


def test_the_corpus_covers_every_domain_the_engine_prescribes_for() -> None:
    """A domain dropping out of the corpus would silently shrink the safety net."""
    corpus_domains = {key.split("/", 1)[0] for key in _corpus()}

    assert corpus_domains == set(GOAL_TEMPLATE_LIBRARY)


@pytest.mark.xfail(
    reason="phases 4-6: 13 of 39 templates declare no exercise slots (e.g. run_threshold, "
    "candidate_library.py:543), so the prescriber falls back to the generic equipment map — "
    "a Running/Threshold day prescribes Air Squat, Push-up, Lunges",
    strict=True,
)
def test_every_template_prescribes_its_own_exercises() -> None:
    """A template that names no exercises cannot be the thing the athlete was promised."""
    slotless = sorted(key for key, entry in _corpus().items() if entry["slot_count"] == 0)

    assert not slotless, f"templates with no exercise slots: {slotless}"
