"""What "easier" and "harder" are allowed to mean, per workout family (phase 3.1).

**Difficulty is not a dose dimension.** It is a family-specific TRANSFORMATION over dose
dimensions. That is why the current global rule — ±1 working set plus an RPE-band shift,
applied to every strength-ish domain alike — is inadequate: it says a hard threshold run and a
hard max-strength day differ from their easy versions in the same way, and they do not.

Resistance-training evidence treats volume, loading/intensity, proximity to failure and rest
as distinct interacting variables rather than interchangeable ways to make a session harder
(Helms et al.; Larsen et al.). The same logic separates a sprinter's recovery from a threshold
runner's: shortening rest between maximal sprints does not make the session harder, it makes
it a different, worse session.

**This module is a contract, not behaviour.** Phase 3.1 declares:

* the dimensions a difficulty transform may touch (``DifficultyDimension``);
* what each family is PERMITTED to touch (``FamilyDifficultyPolicy``);
* the interface a transform implements (``DifficultyTransform``);
* a detector reporting which dimensions a transform actually moved
  (``dimensions_changed``), so 3.3 can assert that a volume-only hardening did not quietly
  raise target intensity.

The only transform implemented here is ``LegacySetStepTransform``, which reproduces today's
behaviour EXACTLY. Real per-family policies arrive in 3.2, and none becomes live until its
consequences under both dose engines have been measured (3.4). The endurance families are
declared and deliberately have no transform: phase 5 supplies real interval and continuous
structure, and inventing running transforms against legacy prose would bake in assumptions
that structure is about to make unnecessary.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast

from app.logic.planning import INTENSITY_CHOICES, intensity_set_delta, normalize_intensity
from app.schemas.workout_structure import (
    ContinuousBlock,
    IntervalBlock,
    StrengthBlock,
    WorkoutStructure,
    adjust_strength_sets,
)

#: easy | medium | hard. Reuses the block-level workload vocabulary rather than inventing a
#: second one; ``normalize_intensity`` is the single parser.
Difficulty = str
DIFFICULTIES: tuple[str, ...] = INTENSITY_CHOICES


class DifficultyDimension(Enum):
    """The levers a difficulty transform can pull. Deliberately not a single scalar."""

    #: How much work: sets, repetitions, accumulated work time.
    VOLUME = "volume"
    #: How hard the work is: load, %e1RM, pace, power, zone.
    INTENSITY = "intensity"
    #: How close to failure: RPE / RIR targets.
    EFFORT = "effort"
    #: How compressed the work is: rest and recovery between efforts.
    DENSITY = "density"
    #: Which movements appear at all.
    EXERCISE_SELECTION = "exercise_selection"


class Permission(Enum):
    """How far a family may move a dimension when difficulty changes."""

    #: Free to move within the family's bounds.
    ALLOWED = "allowed"
    #: May move, but only within limits the family states — e.g. an easy aerobic session
    #: whose intensity must stay aerobic however "hard" the athlete asked for.
    CONSTRAINED = "constrained"
    #: Must not move. A transform that moves it is a bug, not a preference.
    FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class FamilyDifficultyPolicy:
    """What difficulty is allowed to change for one workout family, and why."""

    family: str
    volume: Permission
    intensity: Permission
    effort: Permission
    density: Permission
    exercise_selection: Permission
    rationale: str

    def permission(self, dimension: DifficultyDimension) -> Permission:
        return getattr(self, dimension.value)

    def forbids(self, dimension: DifficultyDimension) -> bool:
        return self.permission(dimension) is Permission.FORBIDDEN


class DifficultyTransform(Protocol):
    """Turn a structure into its easier or harder form, for one family."""

    family: str

    def apply(
        self,
        structure: WorkoutStructure,
        level: Difficulty,
        athlete_context: dict[str, object] | None = None,
    ) -> WorkoutStructure: ...


# --- the declared policies ----------------------------------------------------------
#
# Declarations only in 3.1. A policy with no transform is a statement of intent that 3.2 (or,
# for endurance, phase 5) must satisfy.

POLICIES: dict[str, FamilyDifficultyPolicy] = {
    "strength": FamilyDifficultyPolicy(
        family="strength",
        volume=Permission.ALLOWED,
        intensity=Permission.ALLOWED,
        effort=Permission.ALLOWED,
        density=Permission.CONSTRAINED,
        exercise_selection=Permission.FORBIDDEN,
        rationale=(
            "Sets, load and proximity to failure are all legitimate strength levers. Rest may "
            "move within bounds; cutting it far enough turns a strength session into a "
            "conditioning one. The movements themselves are the session's identity."
        ),
    ),
    "hypertrophy": FamilyDifficultyPolicy(
        family="hypertrophy",
        volume=Permission.ALLOWED,
        intensity=Permission.ALLOWED,
        effort=Permission.ALLOWED,
        density=Permission.CONSTRAINED,
        exercise_selection=Permission.FORBIDDEN,
        rationale="As strength; volume is the primary lever and effort the secondary one.",
    ),
    "max_strength": FamilyDifficultyPolicy(
        family="max_strength",
        volume=Permission.ALLOWED,
        intensity=Permission.ALLOWED,
        effort=Permission.ALLOWED,
        density=Permission.FORBIDDEN,
        exercise_selection=Permission.FORBIDDEN,
        rationale=(
            "Maximal work needs full recovery between efforts. Shortening rest does not make "
            "a max-strength session harder in the intended way — it makes it a different "
            "session at a lower load."
        ),
    ),
    "easy_aerobic": FamilyDifficultyPolicy(
        family="easy_aerobic",
        volume=Permission.ALLOWED,
        intensity=Permission.CONSTRAINED,
        effort=Permission.CONSTRAINED,
        density=Permission.FORBIDDEN,
        exercise_selection=Permission.FORBIDDEN,
        rationale=(
            "An easy aerobic session that gets harder by going faster stops being an easy "
            "aerobic session. Difficulty here is duration; intensity stays inside the zone."
        ),
    ),
    "threshold": FamilyDifficultyPolicy(
        family="threshold",
        volume=Permission.ALLOWED,
        intensity=Permission.CONSTRAINED,
        effort=Permission.CONSTRAINED,
        density=Permission.CONSTRAINED,
        exercise_selection=Permission.FORBIDDEN,
        rationale=(
            "Accumulated work at threshold is the lever. Pace is tightly bounded — threshold "
            "IS a pace — and recovery may shorten only within limits that keep it threshold."
        ),
    ),
    "max_velocity_sprint": FamilyDifficultyPolicy(
        family="max_velocity_sprint",
        volume=Permission.ALLOWED,
        intensity=Permission.FORBIDDEN,
        effort=Permission.FORBIDDEN,
        density=Permission.FORBIDDEN,
        exercise_selection=Permission.FORBIDDEN,
        rationale=(
            "Intensity is already maximal by definition, so it cannot be a lever. Recovery "
            "must NOT be shortened to manufacture difficulty: incomplete recovery lowers "
            "velocity, which is the one quality the session exists to train. Difficulty is "
            "the number of quality repetitions."
        ),
    ),
}


# --- what a transform actually moved -------------------------------------------------


def _strength_signature(block: StrengthBlock) -> dict[DifficultyDimension, object]:
    return {
        DifficultyDimension.VOLUME: block.sets,
        DifficultyDimension.INTENSITY: (block.load_target_kg, block.percent_e1rm),
        DifficultyDimension.EFFORT: (block.rpe_target, block.rir_target),
        DifficultyDimension.DENSITY: (block.rest_sec, block.rest_after_last_set),
        DifficultyDimension.EXERCISE_SELECTION: block.exercise,
    }


def _interval_signature(block: IntervalBlock) -> dict[DifficultyDimension, object]:
    return {
        DifficultyDimension.VOLUME: (
            block.repetitions,
            block.work_duration_sec,
            block.work_distance_m,
        ),
        DifficultyDimension.INTENSITY: (block.intensity_target, block.intensity_basis),
        DifficultyDimension.EFFORT: None,
        DifficultyDimension.DENSITY: (block.recovery_duration_sec, block.recovery_type),
        DifficultyDimension.EXERCISE_SELECTION: block.label,
    }


def _continuous_signature(block: ContinuousBlock) -> dict[DifficultyDimension, object]:
    return {
        DifficultyDimension.VOLUME: (block.duration_sec, block.distance_m),
        DifficultyDimension.INTENSITY: (block.intensity_target, block.intensity_basis),
        DifficultyDimension.EFFORT: None,
        DifficultyDimension.DENSITY: None,
        DifficultyDimension.EXERCISE_SELECTION: block.label,
    }


def _signature(block: object) -> dict[DifficultyDimension, object] | None:
    if isinstance(block, StrengthBlock):
        return _strength_signature(block)
    if isinstance(block, IntervalBlock):
        return _interval_signature(block)
    if isinstance(block, ContinuousBlock):
        return _continuous_signature(block)
    return None


def dimensions_changed(
    before: WorkoutStructure, after: WorkoutStructure
) -> set[DifficultyDimension]:
    """Which dimensions a transform actually moved.

    The basis of every difficulty invariant in 3.3: a volume-only hardening that quietly
    raises the target RPE shows up here as {VOLUME, EFFORT}, and its policy check fails. A
    change in the number of blocks counts as exercise selection — adding work by adding a
    movement is a different act from adding a set.
    """
    changed: set[DifficultyDimension] = set()
    if len(before) != len(after):
        changed.add(DifficultyDimension.EXERCISE_SELECTION)

    for old, new in zip(before, after, strict=False):
        old_sig, new_sig = _signature(old), _signature(new)
        if old_sig is None or new_sig is None or type(old) is not type(new):
            if old != new:
                changed.add(DifficultyDimension.EXERCISE_SELECTION)
            continue
        for dimension, value in old_sig.items():
            if value != new_sig[dimension]:
                changed.add(dimension)
    return changed


def violations(
    before: WorkoutStructure, after: WorkoutStructure, policy: FamilyDifficultyPolicy
) -> list[str]:
    """Dimensions the transform moved that its family forbids."""
    return [
        f"{policy.family} must not change {dimension.value}"
        for dimension in sorted(dimensions_changed(before, after), key=lambda d: d.value)
        if policy.forbids(dimension)
    ]


# --- constraints and validation (phase 3.3) -------------------------------------------
#
# A CONSTRAINED dimension may move, but only inside a bound the family states. A family that
# has not stated one yet is treated as FORBIDDEN by the validator: "we have not decided how
# far this may move" is not permission to move it any distance. That default is deliberately
# conservative and disappears as bounds are written.


class DimensionConstraint(Protocol):
    """Whether a constrained dimension's movement stays inside the family's bound."""

    description: str

    def accepts(self, before: WorkoutStructure, after: WorkoutStructure) -> bool: ...


@dataclass(frozen=True)
class RestWithinFactor:
    """Rest may move, but not so far that the session becomes a different kind of session.

    Halving rest between heavy sets turns strength work into conditioning; doubling it turns a
    hypertrophy session into a strength one. The bound says how far is still the same session.
    """

    lower: float
    upper: float

    @property
    def description(self) -> str:
        return f"rest stays within {self.lower:g}x-{self.upper:g}x of the authored value"

    def accepts(self, before: WorkoutStructure, after: WorkoutStructure) -> bool:
        for old, new in zip(before, after, strict=False):
            if not isinstance(old, StrengthBlock) or not isinstance(new, StrengthBlock):
                continue
            if old.rest_sec is None or new.rest_sec is None:
                if old.rest_sec != new.rest_sec:
                    return False
                continue
            if old.rest_sec == 0:
                return new.rest_sec == 0
            ratio = new.rest_sec / old.rest_sec
            if not self.lower <= ratio <= self.upper:
                return False
        return True


#: (family, dimension) -> the bound. Absent means "not yet stated", which the validator reads
#: as must-not-move rather than as unlimited licence.
#: Cast for the same reason as the transform registry: ``dict`` is invariant, so a dict of a
#: concrete constraint type is not assignable to a dict of the protocol.
CONSTRAINTS: dict[tuple[str, DifficultyDimension], DimensionConstraint] = cast(
    "dict[tuple[str, DifficultyDimension], DimensionConstraint]",
    {
        ("strength", DifficultyDimension.DENSITY): RestWithinFactor(0.75, 1.5),
        ("hypertrophy", DifficultyDimension.DENSITY): RestWithinFactor(0.5, 1.5),
    },
)


def constraint_for(
    family: str, dimension: DifficultyDimension
) -> DimensionConstraint | None:
    return CONSTRAINTS.get((family, dimension))


def validate_transform(
    before: WorkoutStructure,
    after: WorkoutStructure,
    *,
    family: str,
    declared: frozenset[DifficultyDimension] | None = None,
) -> list[str]:
    """Every way this transformation is illegal, each named specifically.

    Three independent checks, because they fail for different reasons and a caller needs to
    know which:

    * **Permission** — the family forbids this dimension outright.
    * **Intent** — the transform moved something it did not declare. A volume-only transform
      that also lowers the RIR target is not "slightly more than advertised"; it is two
      prescription changes reported as one.
    * **Bounds** — a constrained dimension moved further than the family allows, or moved at
      all while the family has stated no bound.

    Scalar dose ordering is deliberately NOT checked here. Whether a legitimate change
    produces more modelled dose is a question about the dose law, not about the legality of
    the prescription — and phase 3.2 measured a case (max strength, hard/easy = 1.02x in both
    engines) where the law barely values a real intensity increase.
    """
    policy = POLICIES[family]
    moved = dimensions_changed(before, after)
    problems: list[str] = []

    for dimension in sorted(moved, key=lambda d: d.value):
        permission = policy.permission(dimension)
        if permission is Permission.FORBIDDEN:
            problems.append(f"{family} must not change {dimension.value}")
            continue
        if declared is not None and dimension not in declared:
            problems.append(
                f"{family} moved {dimension.value}, which this transform did not declare "
                f"(declared: {', '.join(sorted(d.value for d in declared)) or 'nothing'})"
            )
            continue
        if permission is Permission.CONSTRAINED:
            bound = constraint_for(family, dimension)
            if bound is None:
                problems.append(
                    f"{family} moved {dimension.value}, which is constrained with no stated "
                    f"bound — decide the bound before moving it"
                )
            elif not bound.accepts(before, after):
                problems.append(
                    f"{family} moved {dimension.value} outside its bound ({bound.description})"
                )
    return problems


# --- the only transform 3.1 implements ------------------------------------------------


@dataclass(frozen=True)
class LegacySetStepTransform:
    """Today's behaviour, exactly: ±1 working set, floored at one, nothing else touched.

    Wrapped in the contract so the machinery is exercised by the live path from 3.1 onward
    without changing a single prescription. 3.2 replaces it per family; until then this is
    what "harder" means, warts and all — including that it treats max-strength and
    hypertrophy identically, which is the reason phase 3 exists.
    """

    family: str = "legacy_set_step"

    def apply(
        self,
        structure: WorkoutStructure,
        level: Difficulty,
        athlete_context: dict[str, object] | None = None,
    ) -> WorkoutStructure:
        return adjust_strength_sets(structure, intensity_set_delta(normalize_intensity(level)))


LEGACY_TRANSFORM = LegacySetStepTransform()
