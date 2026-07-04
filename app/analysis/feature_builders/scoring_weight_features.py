"""Q8: Scoring weight dataset (all candidates + outcomes).

All considered candidates (not just chosen) joined to their feedback.
Verified: candidate_decision_logs has prescription_decision_id, branch_id,
candidate_type, score_components_json, final_score, chosen, hard_failed.
session_feedback has status, satisfaction_score.

The DB reader (:func:`build_dataset`) stays as-is. The pure helpers below turn a
DB-shaped candidate row into the normalized, leakage-safe shape the offline Q8
scoring-weight pipeline (``app.ml.q8_scoring``) consumes — they take plain dicts
and return plain Python, so the DB layer and the pandas trainer stay decoupled and
this module remains import-light / type-strict.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The eight linear scoring axes on ``SessionCandidate`` (see
# ``app.logic.constraint_engine.candidate``). ORDER IS LOAD-BEARING: the trainer and
# evaluator build weight/feature vectors positionally from this tuple. ``fatigue_penalty``
# and ``tissue_penalty`` are "higher = worse" axes (their production weights are negative).
SCORE_AXES: tuple[str, ...] = (
    "goal_alignment",
    "state_fit",
    "fatigue_penalty",
    "tissue_penalty",
    "novelty_bonus",
    "habit_bonus",
    "template_bias",
    "weak_point_coverage",
)

# Columns that MUST NEVER become model features — they encode the current policy's
# decision or the supervised outcome, so feeding them back in leaks the answer.
FORBIDDEN_FEATURE_COLUMNS: dict[str, str] = {
    "final_score": "the current policy's composite score — a function of the very weights "
    "we are trying to relearn; using it as a feature just re-derives DEFAULT_SCORE_WEIGHTS",
    "chosen": "the current policy's argmax decision. It defines the ranking LABEL / pairs "
    "(which candidate's outcome we observed), never a per-candidate input feature",
    "hard_failed": "a hard-constraint filter flag, not a preference signal; such candidates "
    "bypass scoring entirely and are dropped from the ranked pool",
    "status": "the session outcome — label side",
    "satisfaction_score": "the session outcome — label side",
    "followed_as_prescribed": "the session outcome — label side",
    "pain_flag": "the session outcome — label side",
}

# Athlete-reported satisfaction is a 1..5 Likert (see schemas.session_feedback).
_SAT_MIN, _SAT_MAX = 1.0, 5.0
# A normalized outcome at/above this is a GOOD outcome for the ranking label.
GOOD_OUTCOME_THRESHOLD = 0.60


def parse_score_components(raw: Any) -> dict[str, float]:
    """Parse a ``score_components_json`` value into the eight axes (missing -> 0.0).

    Accepts a JSON string, a mapping, or ``None``. Only the known ``SCORE_AXES`` are
    kept; any extra keys (and the forbidden policy/outcome columns) are ignored.
    """
    if raw is None:
        data: dict[str, Any] = {}
    elif isinstance(raw, str):
        data = json.loads(raw) if raw.strip() else {}
    elif isinstance(raw, dict):
        data = raw
    else:
        data = {}
    return {axis: float(data.get(axis, 0.0) or 0.0) for axis in SCORE_AXES}


def outcome_score(
    satisfaction_score: int | float | None,
    status: str | None,
    *,
    followed_as_prescribed: bool | None = None,
    pain_flag: bool = False,
) -> float:
    """Collapse first-party feedback into a single [0, 1] outcome quality.

    Blends satisfaction (1..5 -> 0..1; neutral 0.5 when unreported), completion status,
    and adherence, then applies a pain penalty. This is the LABEL — it must never appear
    among the features.
    """
    if satisfaction_score is None:
        sat = 0.5
    else:
        sat = (float(satisfaction_score) - _SAT_MIN) / (_SAT_MAX - _SAT_MIN)
        sat = min(1.0, max(0.0, sat))

    s = (status or "").strip().lower()
    status_ok = 1.0 if s == "completed" else (0.5 if s == "modified" else 0.0)

    if followed_as_prescribed is None:
        followed = 0.5
    else:
        followed = 1.0 if followed_as_prescribed else 0.0

    score = 0.55 * sat + 0.25 * status_ok + 0.20 * followed
    if pain_flag:
        score -= 0.30
    return min(1.0, max(0.0, score))


def is_good_outcome(
    satisfaction_score: int | float | None,
    status: str | None,
    *,
    followed_as_prescribed: bool | None = None,
    pain_flag: bool = False,
) -> bool:
    """Binary GOOD-outcome label used as the pairwise ranking target."""
    return (
        outcome_score(
            satisfaction_score,
            status,
            followed_as_prescribed=followed_as_prescribed,
            pain_flag=pain_flag,
        )
        >= GOOD_OUTCOME_THRESHOLD
    )


def normalize_candidate_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize one DB-/fixture-shaped candidate row into the leakage-safe pipeline shape.

    Reads the eight axis columns directly when present, otherwise parses
    ``score_components_json``. The forbidden policy/outcome columns are read ONLY to form
    the ranking label (``good_outcome``) and grouping keys — never copied into the feature
    axes. ``final_score`` is deliberately never surfaced.
    """
    if any(axis in row for axis in SCORE_AXES):
        comps = {axis: float(row.get(axis, 0.0) or 0.0) for axis in SCORE_AXES}
    else:
        comps = parse_score_components(row.get("score_components_json"))

    decision_id = row.get("decision_id", row.get("prescription_decision_id"))
    out: dict[str, Any] = {"decision_id": decision_id, **comps}
    out["chosen"] = bool(row.get("chosen", False))
    out["hard_failed"] = bool(row.get("hard_failed", False))
    out["outcome_score"] = outcome_score(
        row.get("satisfaction_score"),
        row.get("status"),
        followed_as_prescribed=row.get("followed_as_prescribed"),
        pain_flag=bool(row.get("pain_flag", False)),
    )
    out["good_outcome"] = out["outcome_score"] >= GOOD_OUTCOME_THRESHOLD
    return out


async def build_dataset(session: AsyncSession) -> list[dict[str, Any]]:
    """All candidate rows with outcomes for offline policy evaluation (Q8)."""
    query = text("""
        SELECT
            cdl.prescription_decision_id,
            cdl.branch_id,
            cdl.candidate_type,
            cdl.score_components_json,
            cdl.final_score,
            cdl.chosen,
            cdl.hard_failed,
            sf.status,
            sf.satisfaction_score
        FROM candidate_decision_logs cdl
        JOIN prescription_decisions pd ON pd.id = cdl.prescription_decision_id
        LEFT JOIN session_feedback sf ON sf.planned_session_id = pd.planned_session_id
        ORDER BY cdl.prescription_decision_id, cdl.final_score DESC
        LIMIT 500000
    """)
    result = await session.execute(query)
    return [dict(row) for row in result.mappings()]
