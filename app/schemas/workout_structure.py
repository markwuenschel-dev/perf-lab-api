"""What a session IS, as typed blocks (phase 2.1).

Today a prescription is a flat list of exercises, and everything a session actually means —
intervals, work-to-rest ratios, rounds, pacing, warmups — lives in prose: a ``focus`` string
like "4×5 min @ threshold pace / 2 min easy recovery", or a ``reps`` field reading
"30-40 min conversational pace". A model cannot reason about prose, and a client cannot render
it. This module is the typed replacement.

**Scope of 2.1: representation only.** Every existing prescription translates mechanically —

    legacy prescription -> structure -> project_exercises(structure) -> the same exercises[]

— with no workload scaling, no new intervals, no duration recalculation and no template
changes. The blocks below are deliberately wider than 2.1 populates: ``IntervalBlock`` can
already say what a running interval means, and nothing emits one yet. That is on purpose. If
the representation were designed around what lifting needs today, phase 5 would discover that
endurance needs a different schema, and we would migrate twice.

**Structure is the canonical statement of a session.** ``exercises[]`` is a PROJECTION of it
(``project_exercises``), never a second source of truth: a model validator on
``WorkoutPrescription`` rejects any prescription whose exercises disagree with its structure.
In 2.1 structure is still DERIVED from the authored exercises once, at the end of the
pipeline; authorship flips in 2.3, when volume changes start modifying blocks rather than
``duration_min``. The invariant that matters — one writer, never two — holds either way.

**Endurance density stays ``not_modelled``.** Phase 5 defines a temporal work quantity from
real interval/continuous structure; inventing one here would be a third proxy.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.load_explanation import LoadExplanation


class _Block(BaseModel):
    """Common to every block: what it is, and a human label for clients that show one."""

    label: str | None = Field(
        default=None, description="Display name, e.g. 'Main lift' or 'Threshold intervals'."
    )


class StrengthBlock(_Block):
    """Sets of one exercise against a load or effort target.

    The only block 2.1 emits: it is exactly what today's flat ``ExercisePrescription`` says,
    with the fields named rather than implied.
    """

    kind: Literal["strength"] = "strength"
    exercise: str
    sets: int | None = None
    #: Free text in 2.1 ("5", "8-10", "8-12/side"), because that is what templates author
    #: today. Phase 2.2 gives it a typed form; parsing prose here would invent data.
    reps: str | None = None
    load_target_kg: float | None = None
    percent_e1rm: float | None = None
    rpe_target: float | None = None
    rir_target: float | None = None
    rest_sec: int | None = None
    #: Carried through so the projection is LOSSLESS against today's ExercisePrescription.
    #: Without these the projection would quietly drop fields and the agreement validator
    #: would reject every real prescription — which is how the missing one was found.
    load_note: str | None = None
    e1rm_basis_kg: float | None = None
    weak_point_tags: list[str] = Field(default_factory=list)
    #: Provenance for the load suggestion. Not part of what the athlete is asked to do, but
    #: carried so structure loses nothing that ``exercises[]`` holds.
    load_explanation: LoadExplanation | None = None


class IntervalBlock(_Block):
    """Repeated efforts with prescribed recovery — the shape running and HYROX need.

    Emitted by nothing in 2.1. Declared now so phase 5 extends behaviour rather than schema:
    a threshold session is ``repetitions=4, work_duration_sec=300, recovery_duration_sec=120``,
    not the sentence "4×5 min @ threshold pace / 2 min easy recovery".
    """

    kind: Literal["interval"] = "interval"
    repetitions: int | None = None
    work_duration_sec: int | None = None
    work_distance_m: float | None = None
    #: The target and the scale it is expressed on, kept apart: 4.0 means nothing until
    #: ``intensity_basis`` says whether it is a pace, a power, a heart-rate zone or an RPE.
    intensity_target: float | None = None
    intensity_basis: Literal["pace_s_per_km", "watts", "hr_bpm", "zone", "rpe", "percent_e1rm"] | None = None
    recovery_duration_sec: int | None = None
    recovery_type: Literal["passive", "easy", "walk", "jog", "active"] | None = None
    #: The condition that ends the session early — e.g. "stop when pace drops 5%". A quality
    #: stop is a prescription, not a note: the athlete needs to know when to stop.
    quality_stop: str | None = None


class ContinuousBlock(_Block):
    """One unbroken effort: a steady run, a row, a ruck."""

    kind: Literal["continuous"] = "continuous"
    duration_sec: int | None = None
    distance_m: float | None = None
    intensity_target: float | None = None
    intensity_basis: Literal["pace_s_per_km", "watts", "hr_bpm", "zone", "rpe"] | None = None


class WarmupBlock(_Block):
    """Preparation. Carries its own time so calculated duration (2.2) can include it."""

    kind: Literal["warmup"] = "warmup"
    duration_sec: int | None = None
    description: str | None = None


class CooldownBlock(_Block):
    kind: Literal["cooldown"] = "cooldown"
    duration_sec: int | None = None
    description: str | None = None


WorkoutBlock = Annotated[
    StrengthBlock | IntervalBlock | ContinuousBlock | WarmupBlock | CooldownBlock,
    Field(discriminator="kind"),
]

#: A session, in order.
WorkoutStructure = list[WorkoutBlock]
