"""Candidate strength-family difficulty policies (phase 3.2). NOT LIVE.

Three transforms replacing the global "±1 set and shift the RPE band" with per-family
behaviour. They are registered as CANDIDATES and nothing calls them in the prescription path:
3.2 produces prescriptions and their consequences, 3.4 decides what gets promoted.

The families differ in what difficulty is allowed to mean, which is the whole point:

    general strength   easy  0.8× sets, +1 RIR      hard  1.2× sets,  -1 RIR
    hypertrophy        easy  0.8× sets, +1 RIR      hard  1.25× sets, -1 RIR
    max strength       easy  0.8× sets, +1 RIR      hard  volume UNCHANGED, -1 RIR

**Max strength deliberately does not order by set count.** A harder maximal session can carry
FEWER total reps at a heavier load; forcing ``easy < medium < hard`` on sets would encode the
wrong model of the session. Its difficulty is proximity to failure and the load that follows.

**Effort is changed; load is DERIVED.** These transforms move the effort target only, and the
existing load resolution (``strength_calibration.percent_1rm_for_prescription`` →
``suggested_load_kg``) turns that into %e1RM and kilograms. Moving both independently would
double-count one intended intensity change: a lower RIR already means a heavier bar.

**No density manipulation.** The contract permits constrained rest changes for strength and
hypertrophy; these candidates leave rest alone. Volume and effort are enough to test whether
the family abstraction works, and every extra knob makes the measured consequences harder to
attribute.

Proximity to failure is a distinct programming variable, not a synonym for volume
(Baz-Valle et al. 2022; Pelland et al. 2022) — which is why these move separately and why
``dimensions_changed`` reports them separately.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from app.logic.difficulty import Difficulty, DifficultyDimension, DifficultyTransform
from app.logic.planning import INTENSITY_EASY, INTENSITY_HARD, normalize_intensity
from app.schemas.workout_structure import (
    StrengthBlock,
    WorkoutStructure,
    apply_volume_modifier,
)

#: RPE and RIR are the same statement from opposite ends: RIR = 10 − RPE. A transform asks for
#: a change in REPS IN RESERVE, and whichever field the block carries is moved accordingly.
RPE_FLOOR = 1.0
RPE_CEILING = 10.0
RIR_FLOOR = 0.0
RIR_CEILING = 10.0


def _with_effort_delta(block: StrengthBlock, rir_delta: float) -> StrengthBlock:
    """Move a block's effort target by ``rir_delta`` reps in reserve.

    Positive is EASIER (more reps left in the tank). A block carrying an RPE target has it
    moved the other way by the same amount, because one more rep in reserve is one RPE point
    lower. A block stating neither is left alone rather than given an invented target.
    """
    if rir_delta == 0:
        return block
    if block.rir_target is not None:
        return block.model_copy(
            update={"rir_target": max(RIR_FLOOR, min(RIR_CEILING, block.rir_target + rir_delta))}
        )
    if block.rpe_target is not None:
        return block.model_copy(
            update={"rpe_target": max(RPE_FLOOR, min(RPE_CEILING, block.rpe_target - rir_delta))}
        )
    return block


def _transform(
    structure: WorkoutStructure, *, volume_modifier: float, rir_delta: float
) -> WorkoutStructure:
    """Scale volume, then move effort. Load follows from effort downstream — never set here."""
    scaled = apply_volume_modifier(structure, volume_modifier)
    return [
        _with_effort_delta(block, rir_delta) if isinstance(block, StrengthBlock) else block
        for block in scaled
    ]


@dataclass(frozen=True)
class _VolumeAndEffortPolicy:
    """Volume × effort difficulty for one family. Medium is the exact identity."""

    family: str
    easy_volume: float
    easy_rir_delta: float
    hard_volume: float
    hard_rir_delta: float

    @property
    def declared_dimensions(self) -> frozenset[DifficultyDimension]:
        """What this transform claims it may move — the intent the validator holds it to.

        Declaring volume and effort means exactly that: a run that also nudged load or rest
        would be two prescription changes reported as one, and ``validate_transform`` fails it
        by name rather than shrugging at a generic policy violation.
        """
        return frozenset({DifficultyDimension.VOLUME, DifficultyDimension.EFFORT})

    def apply(
        self,
        structure: WorkoutStructure,
        level: Difficulty,
        athlete_context: dict[str, object] | None = None,
    ) -> WorkoutStructure:
        resolved = normalize_intensity(level)
        if resolved == INTENSITY_EASY:
            return _transform(
                structure, volume_modifier=self.easy_volume, rir_delta=self.easy_rir_delta
            )
        if resolved == INTENSITY_HARD:
            return _transform(
                structure, volume_modifier=self.hard_volume, rir_delta=self.hard_rir_delta
            )
        # Medium is the anchor every family shares: the baseline structure, unchanged. Not
        # "equivalent after rounding" — the same values, so a family always has a fixed point.
        return list(structure)


#: General strength: volume and effort move together, modestly.
GENERAL_STRENGTH = _VolumeAndEffortPolicy(
    family="strength",
    easy_volume=0.8,
    easy_rir_delta=1.0,
    hard_volume=1.2,
    hard_rir_delta=-1.0,
)

#: Hypertrophy: a wider volume range, since accumulated work is its primary driver.
HYPERTROPHY = _VolumeAndEffortPolicy(
    family="hypertrophy",
    easy_volume=0.8,
    easy_rir_delta=1.0,
    hard_volume=1.25,
    hard_rir_delta=-1.0,
)

#: Max strength: hard means closer to failure at a heavier load, NOT more sets. Volume is
#: unchanged going up and reduced going down, so set count does not order the three levels.
MAX_STRENGTH = _VolumeAndEffortPolicy(
    family="max_strength",
    easy_volume=0.8,
    easy_rir_delta=1.0,
    hard_volume=1.0,
    hard_rir_delta=-1.0,
)

#: Candidates, not production. The live path still uses LEGACY_TRANSFORM; promotion is 3.4.
#:
#: Cast because ``dict`` is invariant: the policies satisfy ``DifficultyTransform`` structurally,
#: but ``dict[str, _VolumeAndEffortPolicy]`` is not assignable to ``dict[str, DifficultyTransform]``
#: without it. The cast asserts the protocol, which the tests then exercise for real.
CANDIDATE_TRANSFORMS: dict[str, DifficultyTransform] = cast(
    "dict[str, DifficultyTransform]",
    {
        GENERAL_STRENGTH.family: GENERAL_STRENGTH,
        HYPERTROPHY.family: HYPERTROPHY,
        MAX_STRENGTH.family: MAX_STRENGTH,
    },
)
