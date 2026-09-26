"""Session slots declare what a movement must BE, and the catalog decides which one it is.

Templates used to name exercises as literal strings — `("Back Squat", "5", "3")` — which meant
the `exercises` table played no part in choosing anything. Its `equipment_required`,
`weak_point_tags`, `skill_demand` and `sport_domains` were never read by selection, the athlete's
actual equipment was matched against a hard-coded four-key map instead of the catalog, and a name
that drifted from the catalog silently cost the athlete their load prescription and their phi.
`docs/adr/0016-exercise-metadata-layer.md` asks for the opposite: *"prefer data-driven exercise
selection over hard-coded movement branches."*

A slot now states requirements. The catalog answers them.

PINNING IS PART OF THE DESIGN, NOT AN ESCAPE HATCH. A powerlifter's competition squat is not
interchangeable with any other `squat` + `barbell` movement, and an engine that swapped in a box
squat because the metadata matched would be wrong in a way the athlete pays for on the platform.
So a slot may pin to a benchmark lift with ``e1rm_code``; everything else resolves by pattern.
The line is drawn where the sport draws it — competition lifts are pinned, accessories are not.

UNCONFIGURED EQUIPMENT IS NOT "OWNS NOTHING". An athlete who never filled in their equipment has
an empty list, and treating that as "bodyweight only" is the missing-becomes-a-value defect this
codebase keeps removing — it is also the exact bug the old equipment fallback carried, where a
powerlifter with no equipment configured got a bodyweight session instead of squats. An empty
list therefore disables the equipment filter rather than emptying the candidate set.

This module is pure: it takes a catalog snapshot as data and never touches the database, so the
logic layer keeps its DB-free boundary and the resolution is directly unit-testable.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.schemas.workout_structure import (
    CircuitScheme,
    CircuitStation,
    ContinuousBlock,
    IntervalBlock,
)

#: Equipment an athlete is assumed to have without configuring anything.
_ALWAYS_AVAILABLE: frozenset[str] = frozenset({"bodyweight", "none", ""})

#: What each ``AthleteProfile.equipment_preference`` value prefers, as catalog ``load_type``s.
#: "Machines" covers cable machines too; cable *availability* stays its own equipment tag.
EQUIPMENT_PREFERENCE_LOAD_TYPES: dict[str, frozenset[str]] = {
    "barbell": frozenset({"barbell"}),
    "dumbbell": frozenset({"dumbbell"}),
    "machine": frozenset({"machine", "cable"}),
}


def preferred_load_types(preference: Iterable[str] | None) -> frozenset[str]:
    """The load types an equipment preference favours. Unknown values favour nothing."""
    out: set[str] = set()
    for value in preference or ():
        out |= EQUIPMENT_PREFERENCE_LOAD_TYPES.get(value.strip().lower(), frozenset())
    return frozenset(out)


@dataclass(frozen=True)
class CatalogExercise:
    """The slice of an `Exercise` row that selection needs, as plain data.

    Built from the ORM at the service boundary so nothing below it imports a model.
    """

    name: str
    modality: str
    movement_pattern: str
    load_type: str
    pattern_family: str | None = None
    equipment_required: tuple[str, ...] = ()
    sport_domains: tuple[str, ...] = ()
    weak_point_tags: tuple[str, ...] = ()
    skill_demand: float = 0.5
    e1rm_benchmark_code: str | None = None


@dataclass(frozen=True)
class ExerciseSlot:
    """One position in a session, described by requirement rather than by name.

    ``sets`` and ``reps`` stay strings because reps carry prescription prose the engine does not
    model — ``"3-5"``, ``"30-40 min conversational pace"``, ``"40m"``. That is pre-existing and
    orthogonal to selection.

    Every requirement is optional; ``None`` means "no constraint". A slot with no requirements
    at all matches the whole catalog, which is almost never what you want — prefer at least a
    movement pattern.
    """

    sets: str
    reps: str

    #: Pin to the movement carrying this benchmark code. Use for competition lifts ONLY, where
    #: substituting a metadata-equivalent movement would be wrong.
    e1rm_code: str | None = None

    #: Pin to the catalog movement with EXACTLY this name (phase 6.2): resolve it or fail the
    #: slot. No pattern fallback, no closest match, no weak-point substitution — the equipment
    #: check still applies. For event-defined movements (a HYROX station), where "the nearest
    #: carry" would keep a movement category and destroy the session's meaning.
    exercise: str | None = None

    movement_pattern: str | None = None
    pattern_family: str | None = None
    modality: str | None = None
    load_type: str | None = None
    sport_domain: str | None = None

    #: Upper bound on `skill_demand`. Keeps a technical lift out of a slot meant for accessory
    #: work, and out of the hands of an athlete a template has already decided is a novice.
    max_skill_demand: float | None = None

    #: Lower bound on `skill_demand`. A main-lift slot needs this: the default tiebreak prefers
    #: the SIMPLER movement, which is right for accessories and badly wrong for a primary lift —
    #: without a floor, a "max strength squat" slot resolves to a leg extension and a "pull-up"
    #: slot resolves to hanging from the bar, because both are lower-skill members of the same
    #: pattern. The floor says "this position needs a real lift".
    min_skill_demand: float | None = None

    #: The skill level this position wants, ranked by closeness rather than by direction.
    #:
    #: Neither "prefer simpler" nor "prefer harder" works for a main lift. Preferring simpler
    #: resolves a max-strength squat to a leg extension and a pull-up to hanging from the bar;
    #: preferring harder resolves them to an Anderson squat and a ring muscle-up. The movement
    #: a template actually wants sits in the middle — it is the CANONICAL one, and the catalog
    #: has no canonicality field, so its skill level is the available proxy. ``None`` keeps the
    #: accessory default of preferring the simpler movement.
    skill_target: float | None = None

    #: Soft bias — an exercise carrying one of these tags outranks one that does not. Never a
    #: filter, so a slot still resolves when nothing matches.
    prefer_tags: tuple[str, ...] = ()

    #: Allow this slot to pick a movement an earlier slot already used. Needed for a back-off
    #: set of the same lift; off by default so a session does not accidentally repeat.
    allow_repeat: bool = False

    load_note: str | None = None

    #: The WORK SHAPE of an endurance position (phase 5.3): an interval or continuous block
    #: with its timing and intensity basis, and no name. Selection names it after the catalog
    #: pick and the prescriber emits it in place of a strength block. None keeps the position
    #: strength-shaped, as every slot was before. Excluded from the hash: a pydantic model is
    #: not hashable, and the shape is not part of what the slot requires of the catalog.
    endurance: IntervalBlock | ContinuousBlock | None = field(default=None, hash=False)

    def __post_init__(self) -> None:
        if self.exercise is not None and self.e1rm_code is not None:
            raise ValueError("a slot pins by exercise name or by e1RM code, not both")

    def describe(self) -> str:
        """Human-readable requirement, for diagnostics when nothing resolves."""
        if self.exercise:
            return f"exact:{self.exercise}"
        if self.e1rm_code:
            return f"pinned:{self.e1rm_code}"
        parts = [
            f"{k}={v}"
            for k, v in (
                ("pattern", self.movement_pattern),
                ("family", self.pattern_family),
                ("modality", self.modality),
                ("load", self.load_type),
                ("domain", self.sport_domain),
                ("max_skill", self.max_skill_demand),
                ("min_skill", self.min_skill_demand),
            )
            if v is not None
        ]
        return ", ".join(parts) or "unconstrained"


@dataclass
class SlotResolution:
    """What a slot resolved to, and what it had to choose between."""

    slot: ExerciseSlot
    chosen: CatalogExercise | None
    considered: int = 0
    #: Set when nothing satisfied the slot — carried so the caller can report a real reason
    #: rather than silently dropping the movement from the session.
    unmet_reason: str | None = None
    #: True when an equipment preference picked a different movement than the same pool would
    #: have yielded with no preference. Recorded so "the preference changed this" is a measured
    #: fact, not a description of intent.
    preference_changed: bool = False


def equipment_available(
    exercise: CatalogExercise, available: frozenset[str] | None
) -> bool:
    """True when the athlete can perform this movement.

    ``available is None`` means the athlete never configured equipment — unknown, so this
    filter does not apply. That is deliberately different from a configured-but-empty list.
    """
    if available is None:
        return True
    needed = {e.strip().lower() for e in exercise.equipment_required if e and e.strip()}
    needed -= _ALWAYS_AVAILABLE
    return needed <= available


def _matches(slot: ExerciseSlot, ex: CatalogExercise) -> bool:
    """Hard requirements. All must hold; a pinned slot ignores the rest."""
    if slot.exercise is not None:
        return ex.name == slot.exercise
    if slot.e1rm_code is not None:
        return ex.e1rm_benchmark_code == slot.e1rm_code
    if slot.movement_pattern is not None and ex.movement_pattern != slot.movement_pattern:
        return False
    if slot.pattern_family is not None and ex.pattern_family != slot.pattern_family:
        return False
    if slot.modality is not None and ex.modality != slot.modality:
        return False
    if slot.load_type is not None and ex.load_type != slot.load_type:
        return False
    if slot.sport_domain is not None and slot.sport_domain not in ex.sport_domains:
        return False
    if slot.max_skill_demand is not None and ex.skill_demand > slot.max_skill_demand:
        return False
    if slot.min_skill_demand is not None and ex.skill_demand < slot.min_skill_demand:
        return False
    return True


def _rank(
    ex: CatalogExercise,
    slot: ExerciseSlot,
    weak_point_tags: frozenset[str],
    preferred: frozenset[str] = frozenset(),
) -> tuple[float, int, float, str]:
    """Deterministic ordering key. Lower sorts first.

    Bias toward movements that address a flagged deficit — that is the whole reason
    `Exercise.weak_point_tags` exists, and until now selection never read it. An equipment
    preference is the next tie-break: after weak-point and slot-preference matches, before
    skill. It only ORDERS movements that already satisfy the slot and the athlete's equipment,
    so it can neither exclude nor admit one — which says nothing about how often it decides.
    Remaining ties break on skill (simpler first) and then name, so the same athlete and catalog
    always yield the same session; a resolver that shuffled would make every prescriber test flaky.
    """
    tags = {t.lower() for t in ex.weak_point_tags}
    weak_hits = len(tags & {t.lower() for t in weak_point_tags})
    prefer_hits = len(tags & {t.lower() for t in slot.prefer_tags})
    not_preferred = 0 if ex.load_type in preferred else 1
    skill = (
        abs(ex.skill_demand - slot.skill_target)
        if slot.skill_target is not None
        else ex.skill_demand
    )
    return (-(weak_hits * 2 + prefer_hits), not_preferred, skill, ex.name)


def resolve_slot(
    slot: ExerciseSlot,
    catalog: list[CatalogExercise],
    *,
    available_equipment: frozenset[str] | None = None,
    weak_point_tags: frozenset[str] = frozenset(),
    already_used: frozenset[str] = frozenset(),
    preferred_load_types: frozenset[str] = frozenset(),
) -> SlotResolution:
    """Pick the best catalog movement satisfying ``slot``, or report why none did.

    Returns a resolution rather than raising: one unfillable slot should cost that movement and
    say so, not fail the whole session.
    """
    matching = [ex for ex in catalog if _matches(slot, ex)]
    if not matching:
        return SlotResolution(slot, None, 0, f"no catalog movement matches {slot.describe()}")

    afforded = [ex for ex in matching if equipment_available(ex, available_equipment)]
    if not afforded:
        return SlotResolution(
            slot, None, len(matching), f"{slot.describe()} needs equipment the athlete lacks"
        )

    pool = afforded if slot.allow_repeat else [e for e in afforded if e.name not in already_used]
    if not pool:
        # Every candidate is already in this session. Repeating is better than dropping the
        # movement, so fall back rather than return nothing.
        pool = afforded

    pool.sort(key=lambda ex: _rank(ex, slot, weak_point_tags, preferred_load_types))
    chosen = pool[0]
    changed = False
    if preferred_load_types:
        without_preference = min(pool, key=lambda ex: _rank(ex, slot, weak_point_tags))
        changed = without_preference.name != chosen.name
    return SlotResolution(slot, chosen, len(afforded), preference_changed=changed)


def equipment_set(available_equipment: Sequence[str] | None) -> frozenset[str] | None:
    """An athlete's equipment as the resolver reads it.

    An empty or missing list means "never configured", which is NOT "owns nothing": ``None``
    turns the equipment filter off (see :func:`equipment_available`). Only a populated list
    filters.
    """
    if not available_equipment:
        return None
    return frozenset(e.strip().lower() for e in available_equipment if e and e.strip())


def circuit_resolves(
    slots: Sequence[ExerciseSlot],
    circuit: CircuitSpec | None,
    catalog: list[CatalogExercise] | None,
    available_equipment: Sequence[str] | None,
) -> bool:
    """Whether every station of ``circuit`` resolves for this athlete (phase 6.2).

    An authored circuit is atomic: all of its stations, or it is not this session. A template
    whose circuit fails here is ineligible, so the day falls to another variant or is visibly
    replaced, rather than emitting part of a simulation under the simulation's name. With no
    circuit, or no catalog to resolve against, there is nothing to check.
    """
    if circuit is None or catalog is None:
        return True
    stations = list(slots[circuit.first_slot : circuit.first_slot + len(circuit.stations)])
    if len(stations) != len(circuit.stations):
        return False
    resolutions = resolve_slots(
        stations, catalog, available_equipment=equipment_set(available_equipment)
    )
    return all(r.chosen is not None for r in resolutions)


def resolve_slots(
    slots: list[ExerciseSlot],
    catalog: list[CatalogExercise],
    *,
    available_equipment: frozenset[str] | None = None,
    weak_point_tags: frozenset[str] = frozenset(),
    preferred_load_types: frozenset[str] = frozenset(),
) -> list[SlotResolution]:
    """Resolve a session's slots in order, so later slots can avoid earlier picks."""
    used: set[str] = set()
    out: list[SlotResolution] = []
    for slot in slots:
        res = resolve_slot(
            slot,
            catalog,
            available_equipment=available_equipment,
            weak_point_tags=weak_point_tags,
            already_used=frozenset(used),
            preferred_load_types=preferred_load_types,
        )
        if res.chosen is not None:
            used.add(res.chosen.name)
        out.append(res)
    return out


@dataclass(frozen=True)
class CircuitSpec:
    """A run of a template's slots performed as ONE circuit (phase 6.1).

    Slots ``first_slot .. first_slot + len(stations) - 1`` are the stations, in order. Each
    entry of ``stations`` is that station's per-round work shape — reps, distance, time,
    transition — with a placeholder name: selection names it after the catalog pick, exactly
    as an endurance slot's block is named. The scheme says how the stations are run.

    Declared on the template rather than per slot because the scheme belongs to the group,
    not to any one station.
    """

    scheme: CircuitScheme = field(hash=False)
    stations: tuple[CircuitStation, ...] = field(hash=False)
    first_slot: int = 0
    label: str | None = None
    #: Whether the workload preference moves this circuit's rounds (see
    #: ``CircuitBlock.scales_with_workload``). Off unless the session is authored to scale.
    scales_with_workload: bool = False
