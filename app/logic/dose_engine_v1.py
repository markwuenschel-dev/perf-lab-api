"""Dose engine v1 — density means work completed per unit elapsed time.

v0 called two reciprocal quantities "density": the session law computed elapsed MINUTES PER
SET (so a longer session at identical work scored as *denser*) and the per-exercise law
computed SETS PER MINUTE OF REST. Both were raised to the same ``dose_beta``.

**The ruling this module implements (2026-09-18).** Density means work completed per unit
elapsed time:

* fixed work, more elapsed time or rest → **lower** density
* fixed work, less elapsed time or rest → **higher** density
* fixed elapsed time, more work → **higher** density

This matches how the training literature uses the term: density is temporal compression —
accomplishing more work in a given time, or equal work in less time.

**Work is counted in WORKING SETS, not in kg·reps.** A cross-domain "work" unit such as
kg·reps per minute would make a 200 kg deadlift session numerically incomparable with
bodyweight work, running or rowing. Density describes temporal compression only; the
``volume`` and ``intensity`` terms of the dose law already describe how much and how hard.

**The variable is dimensionless.** Raw sets-per-minute would be ~0.2–0.5 for real sessions,
which is not comparable with v0's ~0.35–2.5 and would silently rescale every dose. It is
therefore expressed RELATIVE to a reference pace of one set per
``dose_delta_sets_multiplier`` minutes — the same constant v0 used, read as a reference
rather than as a formula — so a session trained at the reference pace has density 1.0 and the
existing floor/cap keep their meaning.

**The fitted parameters do NOT carry over.** ``dose_beta`` was set against ``minutes_per_set``;
replacing ``x`` with ``1/x`` is not a sign flip, it changes the response surface nonlinearly.
The exponent and every density-dependent coefficient must be re-fit (phase 8), and
``app/engine/parameter_overrides.py`` refuses to apply a v0-fitted dose artifact here.

**v0 is not deleted.** It stays frozen so historical states remain reproducible under the
engine that produced them; only new dose computations use this module.
"""
from __future__ import annotations

from dataclasses import replace

from app.engine.parameters import EngineParameters, default_parameters
from app.logic.dose_engine_v0 import (
    DoseVariables,
)
from app.logic.dose_engine_v0 import (
    calculate_stress_dose as _calculate_stress_dose,
)
from app.logic.dose_engine_v0 import (
    exercise_base_bundle as _exercise_base_bundle,
)
from app.logic.dose_model import NOT_MODELLED as _NOT_MODELLED
from app.logic.dose_model import DensityMeasurement
from app.schemas.workout_structure import (
    ContinuousBlock,
    CooldownBlock,
    IntervalBlock,
    WarmupBlock,
    WorkoutStructure,
)
from app.schemas.workouts import (
    ExerciseEntry,
    ExternalIntensity,
    StressDose,
    WorkoutLog,
)

#: Engine identity, recorded wherever a dose is persisted or compared.
DOSE_ENGINE_VERSION = "dose_engine_v1"

#: Density is NOT MODELLED for this session. The dose law then uses the multiplicative
#: identity, which is not the same statement as "average density" or "the reference pace" —
#: those would be observations, and this is their absence. The basis travels with the value so
#: no future reader can mistake 1.0 for a measurement.
NOT_MODELLED = _NOT_MODELLED

#: Assumed working time per set when an exercise reports rest but not its own duration. Used
#: ONLY inside the explicitly-named proxy below.
ASSUMED_WORK_SECONDS_PER_SET = 30.0


def reference_sets_per_minute(p: EngineParameters) -> float:
    """The pace that reads as density 1.0: one set per ``dose_delta_sets_multiplier`` minutes."""
    return 1.0 / max(1e-6, p.dose_delta_sets_multiplier)


def _clamped(relative: float, p: EngineParameters) -> float:
    return max(p.dose_delta_floor, min(p.dose_delta_cap, relative))


#: Modalities whose work is counted in SETS. Everything else — continuous running above all —
#: performs work the set count cannot describe, and v0's ``max(3, duration/12)`` fallback
#: fabricates one. Measuring sets-per-minute against a fabricated set count would report a
#: 60-minute run as an almost empty session, which is how the first draft of this module made
#: a running plan LOSE aerobic capacity.
SET_COUNTED_MODALITIES = frozenset({"Strength", "Hypertrophy", "Power"})


def session_density_from_parts(
    duration_minutes: float, sets: float, p: EngineParameters
) -> DensityMeasurement:
    """Working sets per minute, relative to the reference pace. Pure arithmetic."""
    if duration_minutes <= 0.0 or sets <= 0.0:
        return NOT_MODELLED
    value = _clamped((sets / duration_minutes) / reference_sets_per_minute(p), p)
    return DensityMeasurement(value=value, basis="sets_per_elapsed_minute")


def session_density(log: WorkoutLog, sets: float, p: EngineParameters) -> DensityMeasurement:
    """Session density, or an explicit neutral when sets are not this session's unit of work.

    Three cases, all deliberate:

    * **Sets reported, set-counted modality** → sets per minute, relative to reference.
    * **Sets not reported** → not modelled. v0 substituted ``max(3, duration/12)``; treating that
      invention as measured work would make density a statement about the fallback.
    * **Running / Mixed** → not modelled here, because a continuous effort's work is distance
      and pace, not sets. Phase 5.4 adds one explicit exception, in the shadow only: timed
      work from an explicitly linked prescription (``prescribed_work_density``), passed in
      by the caller. Nothing in the log alone can produce it.

    A not-modelled measurement contributes the multiplicative identity to the dose product,
    and carries ``basis="not_applicable"`` so it can never be read back as an observation.
    """
    if log.modality not in SET_COUNTED_MODALITIES:
        return NOT_MODELLED
    if log.estimated_sets is None:
        return NOT_MODELLED
    return session_density_from_parts(log.duration_minutes, float(log.estimated_sets), p)


def estimated_exercise_elapsed_minutes(entry: ExerciseEntry) -> float | None:
    """How long an exercise plausibly occupied, work AND inter-set recovery included.

    Returns ``None`` when there is nothing to estimate from, so the caller can fall back
    explicitly rather than inventing a number.

    This is an APPROXIMATION and is named as one: ``rest_seconds`` is per-set rest, and when
    the entry does not carry its own duration the working time is assumed
    (``ASSUMED_WORK_SECONDS_PER_SET``). ``sets / rest`` alone is NOT the physical quantity —
    it ignores the work itself — which is why v0's per-exercise Δ was never density either.
    """
    sets = entry.sets or 0.0
    if sets <= 0.0:
        return None

    if entry.duration_seconds:
        work_seconds = float(entry.duration_seconds)
    elif entry.rest_seconds is not None:
        work_seconds = sets * ASSUMED_WORK_SECONDS_PER_SET
    else:
        return None

    rest_seconds = float(entry.rest_seconds or 0.0) * max(0.0, sets - 1.0)
    elapsed = (work_seconds + rest_seconds) / 60.0
    return elapsed if elapsed > 0.0 else None


def exercise_density_proxy(entry: ExerciseEntry, p: EngineParameters) -> DensityMeasurement:
    """Working sets per estimated elapsed minute for one exercise, relative to reference.

    A proxy, not a measurement: see ``estimated_exercise_elapsed_minutes``. It moves in the
    same direction as ``session_density`` — that agreement is the point, and is pinned by
    tests/properties/test_density_semantics.py.
    """
    sets = entry.sets or 0.0
    elapsed = estimated_exercise_elapsed_minutes(entry)
    if elapsed is None or sets <= 0.0:
        return NOT_MODELLED
    value = _clamped((sets / elapsed) / reference_sets_per_minute(p), p)
    return DensityMeasurement(value=value, basis="sets_per_elapsed_minute")


def reported_volume_sets(log: WorkoutLog, sets: float) -> tuple[float, str]:
    """The set count that enters the volume proxy — only if it was REPORTED, and only where
    sets are the unit of work.

    v0 fed its fabricated fallback, ``max(3, duration/12)``, into ``V`` as though it were
    measured. For a continuous run that meant tuning an unrelated fallback silently moved every
    runner's dose. Here an unreported or non-set-counted session contributes nothing through
    the sets term; its volume is carried by duration and load, which were actually measured.
    The basis is returned so the shadow dataset can tell the three cases apart.
    """
    if log.modality not in SET_COUNTED_MODALITIES:
        return 0.0, "not_counted"
    if log.estimated_sets is None:
        return 0.0, "unreported"
    return float(log.estimated_sets), "reported"


# ---------------------------------------------------------------------------
# Prescribed timed work (phase 5.4) — a SHADOW-ONLY proxy, not performed density
# ---------------------------------------------------------------------------
#
# Temporal work density, not running intensity: a 20-minute easy run and 20 minutes of
# threshold work can share a density while differing completely in intensity and dose. The
# numerator is the prescription's WORK only (interval work, a continuous effort); recovery,
# warmup and cooldown reach the value through the denominator, the logged elapsed time.
#
#   same work, more elapsed time  -> lower density
#   same elapsed time, more work  -> higher density


def prescribed_timed_work_seconds(structure: WorkoutStructure) -> tuple[float | None, str | None]:
    """Seconds of prescribed WORK in ``structure``, or ``(None, reason)`` when it has none.

    Only fully timed endurance work counts. A range ("30-40 min") or distance-only reps have
    no duration, and a partial sum would understate the numerator, so any untimed endurance
    block makes the whole session not modelled. A strength block means this is not an
    endurance session at all.
    """
    if not structure:
        return None, "prescription_has_no_structure"
    work = 0.0
    for block in structure:
        if isinstance(block, WarmupBlock | CooldownBlock):
            continue
        if isinstance(block, IntervalBlock):
            if block.repetitions is None or block.work_duration_sec is None:
                return None, "structure_not_fully_timed"
            work += block.repetitions * block.work_duration_sec
        elif isinstance(block, ContinuousBlock):
            if block.duration_sec is None:
                return None, "structure_not_fully_timed"
            work += block.duration_sec
        else:
            return None, "structure_is_not_endurance"
    if work <= 0.0:
        return None, "no_timed_work"
    return work, None


def prescribed_work_density(work_seconds: float, elapsed_minutes: float) -> DensityMeasurement:
    """Prescribed work seconds / logged elapsed seconds, rejected when physically impossible.

    Never corrected: prescribed work longer than the logged session is an inconsistent
    observation, not a density of 1.18; a missing (zero) elapsed time is missing, not zero.
    """
    elapsed_seconds = elapsed_minutes * 60.0
    if elapsed_seconds <= 0.0:
        return replace(NOT_MODELLED, reason="missing_logged_elapsed")
    if work_seconds > elapsed_seconds:
        return replace(NOT_MODELLED, reason="prescribed_work_exceeds_logged_elapsed")
    return DensityMeasurement(
        value=work_seconds / elapsed_seconds, basis="prescribed_timed_work_over_elapsed"
    )


#: The corrected density variable, injected into the shared dose law. "v1.1" since phase 5.4:
#: v1 can now take a prescribed-work density for endurance sessions, so doses from here on
#: are recorded as such. Without a prescribed density the numbers are identical to "v1".
WORK_PER_TIME_DENSITY = DoseVariables(
    name="v1_work_per_elapsed_time",
    version="v1.1",
    session=session_density,
    entry=exercise_density_proxy,
    volume_sets=reported_volume_sets,
)


def calculate_stress_dose(
    log: WorkoutLog,
    params: EngineParameters | None = None,
    external_intensity: ExternalIntensity | None = None,
    *,
    prescribed_density: DensityMeasurement | None = None,
) -> StressDose:
    """The v0 dose law with the v1 density variable. Same law, corrected input.

    ``prescribed_density`` (phase 5.4, shadow only) replaces the session density of a session
    whose work is NOT counted in sets. For a set-counted session it is ignored: reported sets
    are a measurement and outrank a plan.
    """
    variables = WORK_PER_TIME_DENSITY
    if prescribed_density is not None and log.modality not in SET_COUNTED_MODALITIES:
        measured = prescribed_density

        def _prescribed(_log: WorkoutLog, _sets: float, _p: EngineParameters) -> DensityMeasurement:
            return measured

        variables = replace(WORK_PER_TIME_DENSITY, session=_prescribed)
    return _calculate_stress_dose(
        log,
        params or default_parameters(),
        external_intensity,
        dose_variables=variables,
    )


def exercise_base_bundle(entry: ExerciseEntry, log: WorkoutLog, p: EngineParameters):
    """Per-exercise base under v1 density (shape identical to the v0 helper)."""
    return _exercise_base_bundle(entry, log, p, dose_variables=WORK_PER_TIME_DENSITY)
