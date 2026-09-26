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

from typing import Annotated, Literal, assert_never

from pydantic import BaseModel, Field

from app.schemas.load_explanation import LoadExplanation


class _Block(BaseModel):
    """Common to every block: what it is, a display label, and what follows it.

    ``transition_sec`` is time spent GETTING to the next block — changing station, racking a
    bar, walking to the track. Deliberately not folded into recovery: a HYROX station-to-run
    transition is not prescribed recovery, and phase 6 needs to say so. It is counted once per
    block, after the block's work.
    """

    label: str | None = Field(
        default=None, description="Display name, e.g. 'Main lift' or 'Threshold intervals'."
    )
    transition_sec: int | None = Field(default=None, ge=0)


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
    #: Prescribed rest BETWEEN sets.
    rest_sec: int | None = Field(default=None, ge=0)
    #: Does the prescribed rest follow the final set? Same ambiguity as an interval's last
    #: recovery, and the same explicit default: no. A session ends when the work ends.
    rest_after_last_set: bool = False
    #: How long one set takes to execute. Almost never known today — templates author sets and
    #: reps, not tempo — and that is exactly why duration for a strength block is reported as
    #: INCOMPLETE rather than guessed from a rep-time assumption.
    set_duration_sec: int | None = Field(default=None, ge=0)
    #: Carried through so the projection is LOSSLESS against today's ExercisePrescription.
    #: Without these the projection would quietly drop fields and the agreement validator
    #: would reject every real prescription — which is how the missing one was found.
    load_note: str | None = None
    e1rm_basis_kg: float | None = None
    weak_point_tags: list[str] = Field(default_factory=list)
    #: Provenance for the load suggestion. Not part of what the athlete is asked to do, but
    #: carried so structure loses nothing that ``exercises[]`` holds.
    load_explanation: LoadExplanation | None = None


class _EnduranceIdentity(BaseModel):
    """What an endurance block is called, and how it reads in the legacy ``exercises[]`` view.

    ``activity`` names the work — "Threshold Tempo Run" — in endurance vocabulary rather than a
    strength block's ``exercise``. The ``display_*`` fields and the rest are the COMPATIBILITY
    projection (phase 5.3): exactly the text and values ``exercises[]`` showed before running
    became structured, so making the model honest changed nothing an athlete or a log prefill
    sees. Changing that display is a deliberate UI decision, never a side effect of structure.

    A block with no ``activity`` does not project, like a warmup.
    """

    activity: str | None = Field(default=None, description="What the work is, e.g. 'Tempo Run'.")
    display_sets: int | None = Field(
        default=None, description="Compatibility projection: the legacy exercise's set count."
    )
    display_reps: str | None = Field(
        default=None, description="Compatibility projection: the legacy exercise's reps text."
    )
    load_note: str | None = None
    weak_point_tags: list[str] = Field(default_factory=list)
    rpe_cap: float | None = None
    load_explanation: LoadExplanation | None = None


class IntervalBlock(_EnduranceIdentity, _Block):
    """Repeated efforts with prescribed recovery — the shape running and HYROX need.

    Emitted by running templates since phase 5.3: a threshold session is
    ``repetitions=4, work_duration_sec=300, recovery_duration_sec=120``, not the sentence
    "4×5 min @ threshold pace / 2 min easy recovery".
    """

    kind: Literal["interval"] = "interval"
    repetitions: int | None = Field(default=None, ge=0)
    work_duration_sec: int | None = Field(default=None, ge=0)
    work_distance_m: float | None = Field(default=None, ge=0)
    #: The target and the scale it is expressed on, kept apart: 4.0 means nothing until
    #: ``intensity_basis`` says whether it is a pace, a power, a heart-rate zone or an RPE.
    intensity_target: float | None = None
    intensity_basis: Literal["pace_s_per_km", "watts", "hr_bpm", "zone", "rpe", "percent_e1rm"] | None = None
    recovery_duration_sec: int | None = Field(default=None, ge=0)
    recovery_type: Literal["passive", "easy", "walk", "jog", "active"] | None = None
    #: Does recovery follow the FINAL repetition? 6 × (3 min work / 2 min recovery) is 28
    #: minutes if it does and 26 if it does not, and a session that cannot say which cannot be
    #: timed. Default false: the work is the session, the last recovery is going home.
    recovery_after_last_rep: bool = False
    #: The condition that ends the session early — e.g. "stop when pace drops 5%". A quality
    #: stop is a prescription, not a note: the athlete needs to know when to stop.
    quality_stop: str | None = None


class ContinuousBlock(_EnduranceIdentity, _Block):
    """One unbroken effort: a steady run, a row, a ruck."""

    kind: Literal["continuous"] = "continuous"
    duration_sec: int | None = Field(default=None, ge=0)
    distance_m: float | None = Field(default=None, ge=0)
    intensity_target: float | None = None
    intensity_basis: Literal["pace_s_per_km", "watts", "hr_bpm", "zone", "rpe"] | None = None


class WarmupBlock(_Block):
    """Preparation. Carries its own time so calculated duration (2.2) can include it."""

    kind: Literal["warmup"] = "warmup"
    duration_sec: int | None = Field(default=None, ge=0)
    description: str | None = None


class CooldownBlock(_Block):
    kind: Literal["cooldown"] = "cooldown"
    duration_sec: int | None = Field(default=None, ge=0)
    description: str | None = None


# ---------------------------------------------------------------------------
# Circuits (phase 6.1)
# ---------------------------------------------------------------------------
#
# A circuit is a sequence of STATIONS executed under a SCHEME. The block says what is
# performed; the scheme says how that sequence is run — as many rounds as fit in a cap
# (AMRAP), a fixed number of intervals on a clock (EMOM), a set number of rounds as fast as
# possible (for time), or a set number of rounds at no stated pace (rounds). Formats are
# scheme variants rather than block kinds because they share the stations and differ only in
# how time and volume are defined, and a new format (a ladder, a chipper) extends the scheme
# union, not every reader of the block union.


class CircuitStation(BaseModel):
    """One station of a circuit: what is done there in each round.

    The work quantities are per round and typed; only ``duration_sec`` is time. The rest of
    the fields are the station's compatibility projection into ``exercises[]`` — exactly what
    an ``ExercisePrescription`` carries — so a circuit shows the same list a flat session did.
    """

    exercise: str
    reps: int | None = Field(default=None, ge=0)
    distance_m: float | None = Field(default=None, ge=0)
    #: Time the station's work takes in one round, when the station is timed ("1 min SkiErg").
    duration_sec: int | None = Field(default=None, ge=0)
    #: Time after this station before the next one: changing station, not recovery. The FINAL
    #: station's transition is the gap between rounds, and does not follow the final round.
    transition_sec: int | None = Field(default=None, ge=0)
    display_sets: int | None = Field(
        default=None, description="Compatibility projection: the legacy exercise's set count."
    )
    display_reps: str | None = Field(
        default=None, description="Compatibility projection: the legacy exercise's reps text."
    )
    load_note: str | None = None
    weak_point_tags: list[str] = Field(default_factory=list)
    load_target_kg: float | None = None
    percent_e1rm: float | None = None
    rpe_cap: float | None = None
    e1rm_basis_kg: float | None = None
    load_explanation: LoadExplanation | None = None


class AMRAPScheme(BaseModel):
    """As many rounds as possible inside the cap. The cap IS the elapsed time."""

    format: Literal["amrap"] = "amrap"
    time_cap_sec: int = Field(gt=0)


class EMOMScheme(BaseModel):
    """Work starts on a fixed clock, ``intervals`` times. Elapsed time is the clock's."""

    format: Literal["emom"] = "emom"
    intervals: int = Field(gt=0)
    interval_sec: int = Field(gt=0)


class ForTimeScheme(BaseModel):
    """A fixed amount of work as fast as possible. The athlete's time is the result, not the
    prescription; the cap only bounds it."""

    format: Literal["for_time"] = "for_time"
    rounds: int = Field(gt=0)
    time_cap_sec: int | None = Field(default=None, gt=0)


class FixedRoundsScheme(BaseModel):
    """A fixed number of rounds at no stated pace — e.g. a contrast pair done three times."""

    format: Literal["rounds"] = "rounds"
    rounds: int = Field(gt=0)


CircuitScheme = Annotated[
    AMRAPScheme | EMOMScheme | ForTimeScheme | FixedRoundsScheme,
    Field(discriminator="format"),
]


class CircuitBlock(_Block):
    """Stations performed in order, under a scheme. Projects one exercise per station."""

    kind: Literal["circuit"] = "circuit"
    stations: list[CircuitStation] = Field(min_length=1)
    scheme: CircuitScheme


WorkoutBlock = Annotated[
    StrengthBlock | IntervalBlock | ContinuousBlock | CircuitBlock | WarmupBlock | CooldownBlock,
    Field(discriminator="kind"),
]

#: A session, in order.
WorkoutStructure = list[WorkoutBlock]


# ---------------------------------------------------------------------------
# Timing (phase 2.2) — DESCRIPTIVE ONLY
# ---------------------------------------------------------------------------
#
# Calculated duration explains a workout; it does not prescribe one. Nothing here rewrites
# ``duration_min``, changes selection, or moves a dose: those stay exactly as they were until
# authorship flips in 2.3. What 2.2 buys is the ability to SAY how long a session is, and —
# just as important — to say when we do not know.


class DurationEstimate(BaseModel):
    """How much of a session's time is actually known.

    ``known_seconds`` alone would be a lie by omission: a strength session with rests recorded
    but no set execution time would report the rest as though it were the whole session. So
    the unknown parts are named, and ``complete`` says whether the total can be trusted as a
    duration. An incomplete estimate is never rendered as a number of minutes.
    """

    known_seconds: float = Field(ge=0.0)
    #: What could not be timed, in words, e.g. "Back Squat: set execution time unknown".
    unknown_components: list[str] = Field(default_factory=list)
    #: The most the session can take, when its duration is NOT exact but every unknown part is
    #: capped (a for-time circuit with a time cap). None when the duration is exact (read
    #: ``minutes``) or when any unknown part has no cap. A cap bounds a duration; it is never
    #: added to ``known_seconds`` as though the athlete would take exactly that long.
    upper_bound_seconds: float | None = Field(default=None, ge=0.0)

    @property
    def complete(self) -> bool:
        return not self.unknown_components

    @property
    def minutes(self) -> float | None:
        """The duration in minutes, or None when something is unknown — never a partial sum."""
        return round(self.known_seconds / 60.0, 2) if self.complete else None


def _describe(block: WorkoutBlock, missing: str) -> str:
    name = (
        getattr(block, "exercise", None) or getattr(block, "activity", None)
        or block.label or block.kind
    )
    return f"{name}: {missing}"


def calculate_duration(structure: WorkoutStructure) -> DurationEstimate:
    """Total prescribed time for a structure, and whatever could not be timed.

    The rules, each chosen because the alternative invents data:

    * **Strength** — rest is counted between sets (and after the last only if the block says
      so). Execution time counts only when ``set_duration_sec`` is given; otherwise the block
      is marked unknown rather than assigned an assumed seconds-per-rep.
    * **Interval** — ``repetitions × work_duration_sec``, plus recovery for every gap
      (``repetitions - 1``, or every repetition when ``recovery_after_last_rep``).
    * **Distance-only work is NOT a time quantity.** 5 × 1 km has no duration until a pace
      target can resolve one, which is phase 5's job. Marked unknown here.
    * **Transitions** are counted once per block, separately from recovery.
    * **Circuit** — the scheme's clock owns elapsed time. An AMRAP takes exactly its cap and an
      EMOM ``intervals × interval_sec``; station timing and transitions happen INSIDE that
      clock and are not added to it. A for-time circuit is unknown, because the athlete's time
      is the result; its cap becomes ``upper_bound_seconds``, never known time. Fixed rounds
      are exact only when every station's work and transition time is known.
    """
    known = 0.0
    unknown: list[str] = []
    #: Unknown parts that carry a cap, and the caps' total — the upper bound, when every
    #: unknown part has one.
    capped_parts = 0
    capped_seconds = 0.0

    for block in structure:
        if isinstance(block, WarmupBlock | CooldownBlock):
            if block.duration_sec is None:
                unknown.append(_describe(block, "no duration given"))
            else:
                known += block.duration_sec

        elif isinstance(block, ContinuousBlock):
            if block.duration_sec is not None:
                known += block.duration_sec
            elif block.distance_m is not None:
                unknown.append(_describe(block, "distance-only, no pace target to time it"))
            else:
                unknown.append(_describe(block, "neither duration nor distance"))

        elif isinstance(block, IntervalBlock):
            reps = block.repetitions
            if reps is None:
                unknown.append(_describe(block, "repetitions not given"))
                continue
            if block.work_duration_sec is not None:
                known += reps * block.work_duration_sec
            elif block.work_distance_m is not None:
                unknown.append(_describe(block, "distance-only work, no pace target to time it"))
            else:
                unknown.append(_describe(block, "no work duration or distance"))
            if block.recovery_duration_sec is not None and reps > 0:
                gaps = reps if block.recovery_after_last_rep else reps - 1
                known += max(0, gaps) * block.recovery_duration_sec

        elif isinstance(block, StrengthBlock):
            sets = block.sets
            if sets is None:
                unknown.append(_describe(block, "set count not given"))
                continue
            if block.set_duration_sec is not None:
                known += sets * block.set_duration_sec
            else:
                unknown.append(_describe(block, "set execution time unknown"))
            if block.rest_sec is not None and sets > 0:
                gaps = sets if block.rest_after_last_set else sets - 1
                known += max(0, gaps) * block.rest_sec

        elif isinstance(block, CircuitBlock):
            scheme = block.scheme
            if isinstance(scheme, AMRAPScheme):
                known += scheme.time_cap_sec
            elif isinstance(scheme, EMOMScheme):
                known += scheme.intervals * scheme.interval_sec
            elif isinstance(scheme, ForTimeScheme):
                unknown.append(_describe(block, "for time: the athlete's time is the result"))
                if scheme.time_cap_sec is not None:
                    capped_parts += 1
                    capped_seconds += scheme.time_cap_sec
            elif isinstance(scheme, FixedRoundsScheme):
                seconds = _fixed_rounds_seconds(block.stations, scheme.rounds)
                if seconds is None:
                    unknown.append(_describe(block, "a station's work or transition time unknown"))
                else:
                    known += seconds
            else:
                assert_never(scheme)

        else:
            # Fail closed: a kind with no timing rule must not add 0 s and read as complete.
            assert_never(block)

        if block.transition_sec is not None:
            known += block.transition_sec

    upper_bound = known + capped_seconds if unknown and capped_parts == len(unknown) else None
    return DurationEstimate(
        known_seconds=known, unknown_components=unknown, upper_bound_seconds=upper_bound
    )


def _fixed_rounds_seconds(stations: list[CircuitStation], rounds: int) -> float | None:
    """``rounds`` of ``stations``, or None when any part of it is untimed.

    Each station's transition follows it; the final station's transition is the gap between
    rounds, so it is counted ``rounds - 1`` times and never after the final round.
    """
    *within, last = stations
    if any(s.duration_sec is None for s in stations):
        return None
    if any(s.transition_sec is None for s in within):
        return None
    if rounds > 1 and last.transition_sec is None:
        return None
    per_round = sum(s.duration_sec or 0 for s in stations) + sum(
        s.transition_sec or 0 for s in within
    )
    return rounds * per_round + (rounds - 1) * (last.transition_sec or 0)


# ---------------------------------------------------------------------------
# Volume manipulation (phase 2.3)
# ---------------------------------------------------------------------------


def _scaled_count(count: int, modifier: float, *, floor: int = 1) -> int:
    """Scale a countable quantity, never below ``floor``.

    Rounds half away from zero, so 0.5 × 5 sets is 3 rather than 2: when a modifier lands
    exactly between two integers, the athlete keeps the work. Python's ``round`` is
    banker's rounding and would alternate, making the same modifier behave differently on
    4 sets than on 5.
    """
    from decimal import ROUND_HALF_UP, Decimal

    scaled = Decimal(str(count * modifier)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return max(floor, int(scaled))


def apply_volume_modifier(structure: WorkoutStructure, modifier: float) -> WorkoutStructure:
    """Scale the WORK a structure prescribes, leaving what kind of work it is alone.

    Volume and intensity are separate prescription variables, so this moves volume only:

    * **Strength** — working SETS change; reps, load, %e1RM, RPE/RIR and rest are untouched.
      Changing reps would change the nature of the prescription: 5×3 to 5×8 is not "more
      volume", it is a different session.
    * **Interval** — repetitions change, not the work duration or the intensity target. The
      displayed set count follows the repetitions it mirrors.
    * **Continuous** — accumulated work duration changes.
    * **Warmup / cooldown** — never scaled. Preparation is not training volume.

    ``modifier == 1.0`` returns the structure unchanged, exactly: same objects, same values.

    NOT algebraically reversible. Sets are integers, so applying 0.5 and then 2.0 does not
    promise the original: 5 → 3 → 6. Modifiers describe a prescription for one session, not a
    group operation, and nothing may rely on round-tripping them.
    """
    if modifier == 1.0:
        return list(structure)
    if modifier < 0.0:
        raise ValueError(f"volume modifier must be non-negative, got {modifier}")

    out: WorkoutStructure = []
    for block in structure:
        if isinstance(block, StrengthBlock):
            if block.sets is None:
                out.append(block)
                continue
            out.append(block.model_copy(update={"sets": _scaled_count(block.sets, modifier)}))
        elif isinstance(block, IntervalBlock):
            if block.repetitions is None:
                out.append(block)
                continue
            repetitions = _scaled_count(block.repetitions, modifier)
            update: dict[str, object] = {"repetitions": repetitions}
            # The displayed set count IS the repetition count (phase 5.3); scaling one without
            # the other shows the athlete a different session from the one prescribed.
            if block.display_sets == block.repetitions:
                update["display_sets"] = repetitions
            out.append(block.model_copy(update=update))
        elif isinstance(block, ContinuousBlock):
            if block.duration_sec is None:
                out.append(block)
                continue
            out.append(
                block.model_copy(
                    update={"duration_sec": _scaled_count(block.duration_sec, modifier)}
                )
            )
        elif isinstance(block, CircuitBlock):
            out.append(_scale_circuit(block, modifier))
        elif isinstance(block, WarmupBlock | CooldownBlock):
            out.append(block)
        else:
            # Fail closed: a kind must declare what its volume is before it can be scaled.
            assert_never(block)
    return out


def _scale_circuit(block: CircuitBlock, modifier: float) -> CircuitBlock:
    """A circuit with its scheme's volume lever scaled.

    Which lever is volume: an AMRAP's cap, an EMOM's interval count, a for-time or fixed-rounds
    circuit's rounds. That is a structural fact about each format, not a claim about how far
    easy or hard should move it. A for-time cap is a bound, not volume, and is left alone.
    Stations whose displayed set count mirrors the scaled count follow it, as an interval
    block's does.
    """
    scheme = block.scheme
    if isinstance(scheme, AMRAPScheme):
        cap = _scaled_count(scheme.time_cap_sec, modifier)
        return block.model_copy(update={"scheme": scheme.model_copy(update={"time_cap_sec": cap})})
    if isinstance(scheme, EMOMScheme):
        before, field = scheme.intervals, "intervals"
    elif isinstance(scheme, ForTimeScheme | FixedRoundsScheme):
        before, field = scheme.rounds, "rounds"
    else:
        assert_never(scheme)
    after = _scaled_count(before, modifier)
    stations = [
        s.model_copy(update={"display_sets": after}) if s.display_sets == before else s
        for s in block.stations
    ]
    return block.model_copy(
        update={"scheme": scheme.model_copy(update={field: after}), "stations": stations}
    )


def adjust_strength_sets(structure: WorkoutStructure, delta: int) -> WorkoutStructure:
    """Add or remove working sets on every strength block, never below one.

    The workload preference (easy/medium/hard) moves sets by a fixed step rather than a
    ratio, so it is its own operation. Same rule as above: reps, load and effort targets are
    left exactly as authored.
    """
    if delta == 0:
        return list(structure)
    return [
        block.model_copy(update={"sets": max(1, block.sets + delta)})
        if isinstance(block, StrengthBlock) and block.sets is not None
        else block
        for block in structure
    ]
