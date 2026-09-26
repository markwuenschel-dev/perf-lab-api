"""A characterization snapshot of how every template is SCORED today (phase 4.1).

``test_prescription_goldens.py`` pins what each template prescribes. It does not pin how the
engine chooses between them, and that is what phase 4.2 rewrites: 24 templates are scored by
hand-coded, ``branch_id``-keyed formulas (``candidate_library._DOMAIN_SCORERS``) and are about
to move onto declarative ``ScoringSpec``s. (Done in 4.2 against this file's golden, unchanged.) This file is what lets 4.2 claim "same behaviour,
different representation" as a checked fact rather than an intention.

**Characterization, not correction.** Nothing recorded here is claimed to be right. In
particular the hand-coded scorers AVERAGE the tissues they name (e.g. ``(ankle+knee)/200``)
while the spec path takes the most-stressed one (``max``); the snapshot records that mismatch
exactly, so the migration preserves it and the later average→max decision (4.2b) is a separate,
measured change.

Recorded for EVERY template — eligible or not, so an eligibility regression cannot hide by
making a template vanish from the comparison — at each state of a small grid:

* all eight ``SessionCandidate`` score components and the weighted total from ``score_candidate``;
* the template's own ``state_eligible`` verdict;

plus, per template, its ``kpi_eligible`` and ``goal_eligible`` verdicts over fixed contexts, and
per domain pool and state, the eligible templates in ranked order.

Not recorded: the prescriber's context boosts (``prescriber.py``), which are applied after
``score_template`` and are not what 4.2 touches.

The grid isolates one dimension per state rather than sweeping combinations; what matters is
that every scorer branch and every aggregation is exercised with inputs that tell the
formulas apart (unequal tissues, so mean ≠ max).

Update only when a change is intended: ``pytest tests/test_scoring_goldens.py --golden-update``,
then read the diff and put it in the PR body.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.candidate_library import (
    GOAL_TEMPLATE_LIBRARY,
    CandidateTemplate,
    get_templates,
    score_template,
)
from app.logic.constraint_engine.candidate import score_candidate
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector

GOLDEN_PATH = Path(__file__).parent / "data" / "scoring_goldens.json"

#: Every score component ``SessionCandidate`` carries into ``score_candidate``.
COMPONENTS = (
    "goal_alignment",
    "state_fit",
    "fatigue_penalty",
    "tissue_penalty",
    "novelty_bonus",
    "habit_bonus",
    "template_bias",
    "weak_point_coverage",
)

#: Rounded so a migration that computes the same formula in a different order
#: (``a/200 + b/200`` vs ``(a+b)/200``) is not reported as a behaviour change.
_PLACES = 9

_HEALTHY_CAPACITY = {"aerobic": 300.0, "max_strength": 50.0, "skill": 50.0, "mobility": 50.0}


def _athlete(
    *,
    fatigue: dict[str, float] | None = None,
    tissue: dict[str, float] | None = None,
    capacity: dict[str, float] | None = None,
    habit: float = 0.5,
    squat_skill: float = 0.7,
) -> UnifiedStateVector:
    """Every axis explicit, so a default changing elsewhere cannot move the grid."""
    cx = CapacityState(**{**_HEALTHY_CAPACITY, **(capacity or {})})
    f = FatigueState(**dict.fromkeys(FatigueState.KEYS, 10.0) | (fatigue or {}))
    t = TissueState(**dict.fromkeys(TissueState.KEYS, 10.0) | (tissue or {}))
    return UnifiedStateVector(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=habit,
        skill_state={"squat": squat_skill},
        **sync_legacy_from_vectors(cx, f, t),
    )


#: One dimension per state. Tissue values are deliberately UNEQUAL across the axes the
#: scorers pair up, so an average and a maximum give different numbers.
STATES: dict[str, UnifiedStateVector] = {
    "healthy_baseline": _athlete(),
    "single_tissue_knee": _athlete(tissue={"knee": 85.0}),
    "multi_tissue": _athlete(
        tissue={
            "knee": 80.0, "ankle": 55.0, "lumbar": 70.0, "hip": 35.0, "wrist": 75.0,
            "shoulder": 60.0, "elbow": 45.0, "finger": 65.0,
        }
    ),
    "high_cns_systemic": _athlete(
        fatigue={"cns": 85.0, "muscular": 50.0, "metabolic": 55.0, "structural": 45.0,
                 "tendon": 40.0, "grip": 30.0}
    ),
    "low_readiness": _athlete(fatigue=dict.fromkeys(FatigueState.KEYS, 72.0)),
    "weak_points": _athlete(
        capacity={"aerobic": 150.0, "max_strength": 30.0, "skill": 25.0, "mobility": 25.0},
        fatigue={"grip": 55.0},
    ),
    "mixed_adverse": _athlete(
        fatigue={"cns": 50.0, "muscular": 70.0, "metabolic": 35.0, "structural": 60.0,
                 "tendon": 25.0, "grip": 45.0},
        tissue={"lumbar": 65.0, "knee": 30.0, "shoulder": 55.0, "ankle": 20.0},
        capacity={"aerobic": 180.0},
        habit=0.2,
        squat_skill=0.3,
    ),
}

#: Every KPI any ``kpi_eligible`` predicate reads, on both sides of its threshold.
KPI_CONTEXTS: dict[str, dict[str, float]] = {
    "none": {},
    "below_thresholds": {
        "pl_relative_total": 2.5, "wl_snatch_cj_ratio": 70.0, "run_fatigue_factor": 10.0,
    },
    "above_thresholds": {
        "pl_relative_total": 3.5, "wl_snatch_cj_ratio": 75.0, "run_fatigue_factor": 20.0,
    },
}

#: Every goal any ``goal_eligible`` predicate distinguishes, plus a neutral one.
GOALS = ("", "HalfMarathon", "FullMarathon", "Sprinting", "5K")


def _templates() -> list[tuple[str, CandidateTemplate]]:
    return [
        (f"{domain}/{t.branch_id}", t)
        for domain in sorted(GOAL_TEMPLATE_LIBRARY)
        for t in GOAL_TEMPLATE_LIBRARY[domain]
    ]


def _scored(template: CandidateTemplate, state: UnifiedStateVector) -> dict:
    candidate = score_template(template, state, {})
    row = {c: round(float(getattr(candidate, c)), _PLACES) for c in COMPONENTS}
    row["total"] = round(score_candidate(candidate), _PLACES)
    row["state_eligible"] = template.state_eligible is None or bool(template.state_eligible(state))
    return row


def _rankings() -> dict[str, list[str]]:
    """Per pool and state, the eligible templates best-first — the selection itself."""
    pools = [(domain, "") for domain in sorted(GOAL_TEMPLATE_LIBRARY) if domain != "sprinting"]
    pools.append(("running", "Sprinting"))
    out: dict[str, list[str]] = {}
    for domain, goal in pools:
        for state_name, state in STATES.items():
            eligible = get_templates(domain, {}, goal, state)
            ranked = sorted(
                eligible,
                key=lambda t: (-score_candidate(score_template(t, state, {})), t.branch_id),
            )
            out[f"{domain}{'+' + goal if goal else ''}/{state_name}"] = [
                t.branch_id for t in ranked
            ]
    return out


def _corpus() -> dict:
    templates = {}
    for key, template in _templates():
        # Which scoring PATH a template takes is deliberately not recorded: 4.2 moves all 24
        # hand-scored templates onto specs, and the golden must show that as no change at all.
        templates[key] = {
            "by_state": {name: _scored(template, state) for name, state in STATES.items()},
            "kpi_eligible": {
                name: template.kpi_eligible is None or bool(template.kpi_eligible(kpi))
                for name, kpi in KPI_CONTEXTS.items()
            },
            "goal_eligible": {
                goal or "(none)": template.goal_eligible is None or bool(template.goal_eligible(goal))
                for goal in GOALS
            },
        }
    return {"templates": templates, "rankings": _rankings()}


def test_the_scoring_corpus_matches_its_golden(request) -> None:
    """The safety net for 4.2. A diff here is either intended — and in the PR body — or a defect."""
    current = _corpus()

    if request.config.getoption("--golden-update", default=False):  # pragma: no cover
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", "utf-8")
        pytest.skip("scoring golden rewritten")

    assert GOLDEN_PATH.exists(), (
        f"scoring golden missing — create it with: pytest {Path(__file__).name} --golden-update"
    )
    expected = json.loads(GOLDEN_PATH.read_text("utf-8"))

    for key in sorted(set(expected["templates"]) | set(current["templates"])):
        assert current["templates"].get(key) == expected["templates"].get(key), (
            f"{key}: score or eligibility changed. If intended, rerun with --golden-update "
            "and put the before/after in the PR body."
        )
    assert current["rankings"] == expected["rankings"], "a pool's selection order changed"


# ── the grid is load-bearing, not decorative ────────────────────────────────


def test_the_grid_tells_an_average_from_a_maximum() -> None:
    """If every paired tissue were equal, a mean→max change would pass this golden unseen."""
    tissue = STATES["multi_tissue"].tissue_t
    pairs = [("ankle", "knee"), ("knee", "hip"), ("lumbar", "knee"), ("finger", "elbow"),
             ("shoulder", "elbow"), ("knee", "ankle"), ("ankle", "hip")]

    assert all(getattr(tissue, a) != getattr(tissue, b) for a, b in pairs)


def test_every_eligibility_predicate_is_seen_both_ways() -> None:
    """A predicate the grid never flips is a predicate the golden cannot protect."""
    unflipped = []
    for key, entry in _corpus()["templates"].items():
        template = dict(_templates())[key]
        verdict_sets = []
        if template.state_eligible is not None:
            verdict_sets.append(("state", {r["state_eligible"] for r in entry["by_state"].values()}))
        if template.kpi_eligible is not None:
            verdict_sets.append(("kpi", set(entry["kpi_eligible"].values())))
        if template.goal_eligible is not None:
            verdict_sets.append(("goal", set(entry["goal_eligible"].values())))
        unflipped += [f"{key} ({kind})" for kind, seen in verdict_sets if seen != {True, False}]

    assert not unflipped, f"predicates the grid never flips: {unflipped}"


def test_tissue_state_moves_only_the_tissue_penalty() -> None:
    """Tissue load reaches a score through ``tissue_penalty`` alone — never eligibility or
    any other component. So a change to how tissues are aggregated (phase 4.2b: mean → max)
    can move only that component, the weighted total, and therefore the ranking.
    """
    loaded_tissues = STATES["multi_tissue"]
    fresh_tissues = STATES["healthy_baseline"]  # same fatigue, capacity and habit
    untouched = [c for c in COMPONENTS if c != "tissue_penalty"] + ["state_eligible"]
    moved = []
    for key, template in _templates():
        loaded, fresh = _scored(template, loaded_tissues), _scored(template, fresh_tissues)
        moved += [f"{key}.{c}" for c in untouched if loaded[c] != fresh[c]]

    assert not moved, f"tissue state leaked into: {moved}"


# ── the inventory, as executable facts ──────────────────────────────────────


def test_the_template_inventory() -> None:
    """The counts phase 4 is planned against. A drift here re-opens that plan."""
    templates = [t for _, t in _templates()]
    ids = Counter(t.branch_id for t in templates)

    assert len(templates) == 45
    assert sum(1 for t in templates if t.exercise_slots) == 37
    assert sum(1 for t in templates if not t.exercise_slots) == 8
    # 13 before phase 4.2; the other 24 were scored by branch_id-keyed domain functions.
    assert all(t.scoring is not None for t in templates)
    # Phase 4.4 renamed the calisthenics copy of gym_skill to cal_skill; no id is shared now.
    # Phase 5.6 added run_recovery and power_potentiation (39 / 30 slotted), each reachable
    # only on its own planned day. Phase 6.2 added six HYROX / CrossFit templates on three
    # owned days (45 / 36 slotted), and gave metcon_engine its authored bike session
    # (37 slotted, 8 slot-less).
    assert not {branch for branch, n in ids.items() if n > 1}


# ── the catch-all 4.2 removed ───────────────────────────────────────────────


def test_a_template_without_a_scoring_spec_is_refused_rather_than_guessed() -> None:
    """A new template must declare how it is scored, not inherit a neighbour's formula.

    Before phase 4.2 this template was accepted and scored as ``run_sprint`` — the last return
    of the running scorer. Refusal at construction or at scoring both count: either way no
    score is invented.
    """
    with pytest.raises((TypeError, ValueError)):
        newcomer = CandidateTemplate(  # pyright: ignore[reportCallIssue] — the point of the test
            type="Hill Repeats", focus="8×60s uphill", rationale="new", branch_id="run_hills",
            duration_min=45, goal_alignment=0.8, domain="running",
        )
        score_template(newcomer, STATES["healthy_baseline"], {})
