"""P4-1: one rule for how a set's RPE and RIR combine (ADR-0056).

Eight sites used to interpret effort, and for the same set they disagreed about which of RPE and
RIR wins, what "to failure" means and what counts as informative. ``resolve_effort`` is the single
rule; the evidence gate, the failure test and the intensity ladder all call it.

Pinned here:

* the resolver's table, including the 0.5 agreement boundary and the 9.5 failure boundary;
* the intensity ladder is unchanged (RPE still wins; ``5 @ 2RIR`` equals ``5 @ RPE8``);
* the gate's **deliberate** changes: a set whose RPE and RIR contradict each other is no longer
  evidence, and where both are given the RPE decides (it used to be "either passes");
* the read-time gate applies the same rule to rows already stored;
* through the real writer: a contradicting set yields no e1RM evidence, a consistent one does;
* no other module implements the effort rule (an architecture test with a short, justified
  allowlist: the frozen v0 dose law is the one documented exception).
"""

from __future__ import annotations

import ast
from fractions import Fraction
from pathlib import Path

import pytest
from sqlalchemy import select

from app.logic import strength_calibration as sc
from app.logic import strength_evidence as se
from app.logic.prescription_evidence import clears_qualifying_set_gate
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.user import User
from app.schemas.benchmarks import (
    BenchmarkObservationCreate,  # noqa: F401  (documents the other writer)
)
from app.schemas.workouts import WorkoutLog, WorkoutSetEntry
from app.services import benchmark_service  # noqa: F401
from app.services.state_service import process_new_workout

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# The resolver
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("rpe", "rir", "eff", "conflict", "failure"),
    [
        (None, None, None, False, False),  # unknown
        (8.0, None, 8.0, False, False),  # RPE only
        (None, 2.0, 8.0, False, False),  # RIR only: 10 - rir
        (None, 0.0, 10.0, False, True),  # RIR 0 is a failure set
        (8.0, 2.0, 8.0, False, False),  # agree
        (7.5, 2.0, 7.5, False, False),  # 0.5 apart: still agree, RPE used
        (8.5, 2.0, 8.5, False, False),  # 0.5 apart the other way
        (7.4, 2.0, 7.4, True, False),  # 0.6 apart: conflict, RPE kept
        (8.0, 0.0, 8.0, True, False),  # the review's example: rpe 8 vs rir 0
        (6.0, 0.0, 6.0, True, False),
        (9.5, 0.5, 9.5, False, True),  # exactly the failure threshold
        (9.49, None, 9.49, False, False),  # just under it
        (None, 0.5, 9.5, False, True),
        (None, 0.51, 9.49, False, False),
    ],
)
def test_the_resolver_table(rpe, rir, eff, conflict, failure):
    e = sc.resolve_effort(rpe, rir)
    assert (e.eff_rpe, e.conflict, e.to_failure) == (pytest.approx(eff) if eff is not None else None, conflict, failure)
    assert e.known is (eff is not None)
    assert e.admissible is (eff is not None and not conflict)


def test_an_explicit_failure_statement_counts_without_any_effort():
    e = sc.resolve_effort(None, None, explicit_failure=True)
    assert (e.eff_rpe, e.to_failure, e.known) == (None, True, False)  # failure, but no RPE figure
    assert sc.resolve_effort(7.0, None, explicit_failure=True).to_failure is True


def test_failure_is_continuous_across_the_threshold():
    below, at = sc.resolve_effort(9.49, None), sc.resolve_effort(9.5, None)
    assert (below.to_failure, at.to_failure) == (False, True)


# --------------------------------------------------------------------------- #
# The intensity ladder keeps behaving as it did
# --------------------------------------------------------------------------- #

def _intensity(**kw):
    base: dict[str, object] = {
        "reps": 5.0, "load_kg": 100.0, "rpe": None, "rir": None, "e1rm_pre": None, "to_failure": False,
    }
    return sc.external_intensity_for_set(**{**base, **kw})  # type: ignore[arg-type]


def test_five_at_two_rir_is_five_at_rpe_eight():
    by_rir = _intensity(rir=2.0)
    by_rpe = _intensity(rpe=8.0)
    assert (by_rir.value, by_rir.source) == (by_rpe.value, by_rpe.source) == (pytest.approx(0.811), "rpe_rir_chart")


def test_when_rpe_and_rir_disagree_the_ladder_still_uses_the_rpe():
    both = _intensity(rpe=8.0, rir=0.0)
    assert both.value == _intensity(rpe=8.0).value != _intensity(rir=0.0).value


def test_no_effort_is_still_neutral_not_a_guess():
    r = _intensity()
    assert (r.value, r.source) == (1.0, "neutral_missing")


# --------------------------------------------------------------------------- #
# The gate: what is deliberately different
# --------------------------------------------------------------------------- #

def _exact_gate(reps, rpe, rir, fidelity) -> bool:
    """The gate's rule restated independently, in exact arithmetic (inputs are decimal strings):
    1-5 reps; effort known; RPE decides where both are given and they agree within 0.5 (a
    contradiction is not evidence); the bar is RPE 8 for per-set effort and 9 below it."""
    if reps is None or reps < 1 or reps > 5 or (rpe is None and rir is None):
        return False
    r = Fraction(rpe) if rpe is not None else None
    i = Fraction(rir) if rir is not None else None
    if r is not None and i is not None:
        if abs(r - (10 - i)) > Fraction(1, 2):
            return False
        eff = r
    else:
        eff = r if r is not None else 10 - i  # type: ignore[operator]
    return eff >= (8 if fidelity == se.FIDELITY_SET_LEVEL else 9)


_GRID_RPE = [None, "6", "6.5", "7", "7.4", "7.5", "7.6", "8", "8.3", "8.4", "8.5", "8.6", "9", "9.3", "9.5", "10"]
_GRID_RIR = [None, "0", "0.5", "1", "1.5", "2", "2.2", "2.5", "3", "4"]


@pytest.mark.parametrize("fidelity", [se.FIDELITY_SET_LEVEL, "group_level", "missing"])
def test_the_gate_gives_the_exact_expected_answer_for_every_combination(fidelity):
    """Every cell, both ways: an agreeing pair must still be accepted (not just a bad one
    rejected), an unambiguous single field must be unchanged, a contradiction must be refused."""
    for reps in (None, 0, 1, 3, 5, 6):
        for rpe in _GRID_RPE:
            for rir in _GRID_RIR:
                got = se.is_e1rm_informative(
                    reps, float(rpe) if rpe else None, float(rir) if rir else None, fidelity
                )
                assert got is _exact_gate(reps, rpe, rir, fidelity), (reps, rpe, rir, fidelity)


@pytest.mark.parametrize("fidelity", [se.FIDELITY_SET_LEVEL, "group_level", "missing"])
def test_a_single_field_is_judged_exactly_as_the_old_rule_judged_it(fidelity):
    """Where only one of RPE/RIR is given nothing changed."""
    def old(rpe, rir):
        if fidelity != se.FIDELITY_SET_LEVEL:
            return (rpe is not None and rpe >= 9.0) or (rir is not None and rir <= 1.0)
        return (rpe is not None and rpe >= 8.0) or (rir is not None and rir <= 2.0)

    for rpe in _GRID_RPE:
        for rir in _GRID_RIR:
            if (rpe is None) == (rir is None):
                continue  # none or both
            a, b = (float(rpe) if rpe else None), (float(rir) if rir else None)
            assert se.is_e1rm_informative(3, a, b, fidelity) is bool(old(a, b)), (rpe, rir)


def test_every_agreeing_decimal_pair_is_accepted_and_every_contradiction_refused():
    """The 0.5 agreement boundary on decimal inputs, checked against exact arithmetic over the
    whole 0.1-step plane. (8.3, 2.2) subtracts to 0.5000000000000009 in floats; it is 0.5."""
    checked = 0
    for x in range(60, 101):
        for y in range(0, 41):
            rpe, rir = f"{x // 10}.{x % 10}", f"{y // 10}.{y % 10}"
            expected = abs(Fraction(rpe) - (10 - Fraction(rir))) > Fraction(1, 2)
            assert sc.resolve_effort(float(rpe), float(rir)).conflict is expected, (rpe, rir)
            checked += 1
    assert checked == 41 * 41


@pytest.mark.parametrize(
    ("rpe", "rir", "conflict"),
    [
        (8.3, 2.2, False),  # the review's repro: float gap 0.5000000000000009
        (8.2, 2.3, False),
        (7.7, 2.2, False),
        (8.31, 2.2, True),  # 0.51
        (8.3000001, 2.2, True),  # 0.5000001: genuinely past it
        (8.6, 2.1, True),
        (7.2, 2.2, True),
    ],
)
def test_the_decimal_agreement_boundary_in_both_directions(rpe, rir, conflict):
    assert sc.resolve_effort(rpe, rir).conflict is conflict
    assert se.is_e1rm_informative(5, rpe, rir, se.FIDELITY_SET_LEVEL) is (not conflict and rpe >= 8.0)


@pytest.mark.parametrize(
    ("rpe", "rir"),
    [
        (9.0, 4.0),  # RPE says near failure, RIR says four in reserve
        (6.0, 0.0),  # RPE says easy, RIR says failure
        (8.0, 0.0),
    ],
)
def test_a_set_whose_rpe_and_rir_contradict_each_other_is_not_evidence(rpe, rir):
    assert se.is_e1rm_informative(5, rpe, rir, se.FIDELITY_SET_LEVEL) is False
    assert se.is_e1rm_informative(5, rpe, None, se.FIDELITY_SET_LEVEL) is (rpe >= 8.0)  # alone, RPE may qualify
    assert se.is_e1rm_informative(5, None, rir, se.FIDELITY_SET_LEVEL) is (rir <= 2.0)


def test_where_both_are_given_the_rpe_decides():
    # 7.5 and 2 reps in reserve agree (0.5 apart). The old rule passed it on the RIR alone.
    assert se.is_e1rm_informative(5, 7.5, 2.0, se.FIDELITY_SET_LEVEL) is False
    assert se.is_e1rm_informative(5, 8.0, 2.0, se.FIDELITY_SET_LEVEL) is True
    assert se.is_e1rm_informative(5, 8.5, 2.0, se.FIDELITY_SET_LEVEL) is True


def test_the_quick_entry_bar_is_still_stricter():
    assert se.is_e1rm_informative(5, 8.5, None, se.FIDELITY_SET_LEVEL) is True
    assert se.is_e1rm_informative(5, 8.5, None, "group_level") is False
    assert se.is_e1rm_informative(5, 9.0, None, "group_level") is True


def test_the_read_time_gate_applies_the_same_rule_to_stored_rows():
    def gate(rpe, rir):
        return clears_qualifying_set_gate(
            reps=5, load_kg=100.0, rpe=rpe, rir=rir, effort_fidelity=se.FIDELITY_SET_LEVEL
        )

    assert gate(9.0, 4.0) is False  # a stored row that contradicts itself no longer sizes a load
    assert gate(9.0, None) is True


# --------------------------------------------------------------------------- #
# Through the real writer
# --------------------------------------------------------------------------- #

async def _squat_day(db, email: str, *, rpe, rir) -> list[BenchmarkObservation]:
    from datetime import UTC, datetime

    from app.models.benchmark_definition import BenchmarkDefinition

    user = User(email=email, hashed_password="x", is_active=True)
    db.add(user)
    db.add(Exercise(name="Back Squat", modality="Strength", movement_pattern="squat",
                    load_type="barbell", is_benchmark=True, e1rm_benchmark_code="pl_e1rm_squat"))
    db.add(BenchmarkDefinition(
        code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting", metric_type="load",
        unit="kg", better_direction="higher", observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    ))
    await db.commit()
    await db.refresh(user)
    await process_new_workout(db, user.id, WorkoutLog(
        timestamp=datetime.now(UTC), modality="Strength", duration_minutes=45.0, session_rpe=8.0,
        sets=[WorkoutSetEntry(exercise_name="Back Squat", sets=1, load_kg=100.0, reps=5, rpe=rpe, rir=rir)],
    ), received_at=datetime.now(UTC))
    return list((await db.execute(select(BenchmarkObservation))).scalars())


async def test_a_consistent_near_failure_set_yields_evidence(async_db):
    rows = await _squat_day(async_db, "eff-ok@test.com", rpe=8.5, rir=1.5)
    assert len(rows) == 1 and rows[0].rpe == 8.5 and rows[0].rir == 1.5


async def test_a_contradicting_set_yields_no_evidence_but_the_workout_is_kept(async_db):
    rows = await _squat_day(async_db, "eff-bad@test.com", rpe=9.0, rir=4.0)
    assert rows == []
    from app.models.workout_set_log import WorkoutSetLog
    sets = (await async_db.execute(select(WorkoutSetLog))).scalars().all()
    assert len(sets) == 1 and (sets[0].rpe, sets[0].rir) == (9.0, 4.0)  # stored verbatim


# --------------------------------------------------------------------------- #
# Nobody else implements the rule
# --------------------------------------------------------------------------- #

# Files allowed to spell out `10 - rir` or the failure threshold, and why.
_ALLOWED = {
    "app/logic/strength_calibration.py": "the resolver itself",
    "app/logic/dose_engine_v0.py": (
        "the frozen v0 dose law: session/entry F = (10 - avg_rir)/10. Changing it is v1 work "
        "(ADR-0039/0056 note); it is a dose-shape rule, not a set-effort interpretation"
    ),
    "app/logic/difficulty_strength.py": "RIR_FLOOR = 10 - INTENSITY_RPE_CEILING: a planning-difficulty constant",
    "app/scripts/load_hit_strength.py": "offline dataset ingest: converts a published RPE column to RIR",
}


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
    }


def _module_numbers(tree: ast.AST) -> dict[str, float]:
    """Module-level ``NAME = <number>``, so ``rpe >= FAILURE_THRESHOLD`` resolves to 9.5."""
    out: dict[str, float] = {}
    for node in getattr(tree, "body", []):
        if (
            isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float))
        ):
            out[node.targets[0].id] = float(node.value.value)
    return out


def _number(node: ast.AST, consts: dict[str, float]) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


def _implements_the_effort_rule(tree: ast.AST) -> list[int]:
    """Line numbers where a module spells out the effort rule in one of the syntactic forms below.

    What this guards, and what it does not: it recognizes ``10 - <rir>`` (the literal, or a module
    constant equal to 10), any comparison of an ``rpe`` expression with 9.5 (the literal or a
    module constant, either operand order, any operator), and any comparison of ``rir`` /
    ``avg_rir`` with 0 (either order). It is a guard against those patterns, not a proof that no
    one re-derives the rule some other way; a new spelling needs a probe added below.
    """
    consts = _module_numbers(tree)
    hits: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub)
            and _number(node.left, consts) == 10.0
            and any("rir" in n.lower() for n in _names(node.right))
        ):
            hits.append(node.lineno)
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for a, b in zip(operands, operands[1:], strict=False):
                for side, other in ((a, b), (b, a)):
                    if _number(other, consts) == 9.5 and any("rpe" in n.lower() for n in _names(side)):
                        hits.append(node.lineno)
                    if _number(other, consts) == 0.0 and any(
                        n.lower() in ("rir", "avg_rir") for n in _names(side)
                    ):
                        hits.append(node.lineno)
    return sorted(set(hits))


def test_no_other_module_implements_the_effort_rule():
    offenders: dict[str, list[int]] = {}
    for path in sorted((ROOT / "app").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in _ALLOWED:
            continue
        lines = _implements_the_effort_rule(ast.parse(path.read_text(encoding="utf-8")))
        if lines:
            offenders[rel] = lines
    assert offenders == {}, (
        "effort is interpreted in one place, sc.resolve_effort (ADR-0056); call it instead of "
        f"spelling out `10 - rir` or the failure threshold: {offenders}"
    )


@pytest.mark.parametrize(
    "source",
    [
        "def f(rir):\n    return 10.0 - rir\n",
        "def f(avg_rir):\n    return 10 - avg_rir\n",
        "TEN = 10.0\ndef f(rir):\n    return TEN - rir\n",
        "def f(rpe):\n    return rpe >= 9.5\n",
        "def f(rpe):\n    return 9.5 <= rpe\n",
        "def f(rpe):\n    return rpe > 9.5\n",
        "FAILURE_THRESHOLD = 9.5\ndef f(rpe):\n    return rpe >= FAILURE_THRESHOLD\n",
        "def f(s):\n    return s.avg_rpe >= 9.5\n",
        "def f(rir):\n    return rir <= 0\n",
        "def f(rir):\n    return 0 >= rir\n",
        "def f(r):\n    return r.rir == 0\n",
    ],
)
def test_the_scanner_recognizes_each_spelling_it_claims_to(source):
    assert _implements_the_effort_rule(ast.parse(source)) != []


@pytest.mark.parametrize(
    "source",
    [
        "def f(rir, rpe):\n    return sc.resolve_effort(rpe, rir).to_failure\n",
        "def f(x):\n    return 10.0 - x, x >= 9.5, x <= 0\n",  # unrelated names
        "def f(rpe):\n    return rpe >= 8.0\n",  # a different threshold
        "def f(rir):\n    return max(0.0, rir)\n",  # no comparison with 0
    ],
)
def test_the_scanner_leaves_unrelated_code_alone(source):
    assert _implements_the_effort_rule(ast.parse(source)) == []
