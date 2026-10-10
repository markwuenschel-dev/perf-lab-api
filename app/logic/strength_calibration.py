"""The one canonical, versioned %1RM ↔ load calibration service (ADR-0056).

Before this module, three slightly different Epley forms lived in the tree, so the
same set was interpreted differently depending on which subsystem read it — a
production landmine (prescribed load and dose-inferred intensity for one set could
disagree by a few %). This module is the **only** place allowed to turn a
``reps / load / RPE / RIR`` observation into a ``%1RM`` or an external intensity.
Prescription, e1RM extraction, and the ADR-0039 dose intensity fallback all call it.

Every public result carries its ``source`` + ``confidence`` + ``model_version`` so a
downstream consumer never mistakes an estimate for a measurement (the honesty ladder
as data). Consumers may *transform* a returned intensity (different exponents/weights)
but may **not** recompute it with a divergent formula or a hidden fallback.

Model ladder (highest fidelity first) — see :func:`external_intensity_for_set`:

1. **Actual relative load** — ``load / e1RM_pre`` (dose only; primary when a pre-log
   e1RM exists, ADR-0055).
2. **RPE/RIR chart** — a static, versioned ``reps × RPE → %1RM`` table (RTS/Helms
   shaped). Primary for prescription; the dose fallback when effort is known.
3. **Reps-beyond-first Epley** — used **only** when the set is to-failure / AMRAP.
4. **Movement / program default** (not yet configured).
5. **Neutral / missing** — labeled ``neutral_missing``, zero confidence.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bumped whenever any calibration formula or the chart changes. Emitted everywhere.
MODEL_VERSION = "rpe_rir_chart_v1"

# --- Ladder rung / source labels (persisted as text; never a bare number) --------
SRC_RELATIVE_LOAD = "relative_load"
SRC_RPE_RIR_CHART = "rpe_rir_chart"
SRC_EPLEY_FAILURE = "epley_failure"
SRC_MOVEMENT_DEFAULT = "movement_default"
SRC_NEUTRAL_MISSING = "neutral_missing"

# --- Bounds ----------------------------------------------------------------------
PCT_MIN = 0.30
PCT_MAX_DOSE = 1.05          # dose intensity may exceed 1.0 (a grinder past e1RM)
PCT_MAX_PRESCRIPTION = 1.00  # never prescribe >100% e1RM without an overload protocol

# Base per-rung confidence, before the effort-fidelity multiplier (ADR-0045).
_CONF_RELATIVE_LOAD = 0.90
_CONF_RPE_RIR_CHART = 0.60
_CONF_EPLEY_FAILURE = 0.40
_CONF_NEUTRAL = 0.0

# Effort fidelity → confidence multiplier (ADR-0045). Strictly monotone in the
# documented ladder: set_level > group_level > session_level > missing. A rung the
# contract calls less trustworthy may never score as more trustworthy.
#
# `missing` is deliberately NOT 0.0 (unlike the authority-side FIDELITY_MULTIPLIER in
# strength_evidence): this scales *confidence*, and the top ladder rung — relative load
# (load / e1RM_pre) — carries genuine signal that does not depend on effort at all.
# Zeroing it would assert "we know nothing" about a set whose intensity we measured.
FIDELITY_CONF_MULTIPLIER: dict[str, float] = {
    "set_level": 1.0,
    "group_level": 0.6,
    "session_level": 0.4,
    "missing": 0.2,
}

# ---------------------------------------------------------------------------------
# Effort: ONE rule for how RPE and RIR combine (P4-1). Every consumer that reads a set's
# effort calls resolve_effort(); none may implement its own `10 - rir` or failure threshold
# (tests/test_effort_resolver.py::test_no_other_module_implements_the_effort_rule).
# ---------------------------------------------------------------------------------

# RPE and RIR describe the same thing (RPE = 10 - RIR). Within this many RPE steps they
# agree (the boundary is inclusive); further apart they conflict.
EFFORT_AGREEMENT_TOLERANCE = 0.5
# Decimal inputs do not subtract exactly: 8.3 - (10 - 2.2) is 0.5000000000000009 in floats. A
# difference this close to the boundary is rounding, not disagreement; anything the API can
# send that is genuinely beyond it (0.51, 0.5000001) is still far larger than this.
_EFFORT_ROUNDING_SLACK = 1e-9
# A set at or beyond this effort is treated as taken to failure.
FAILURE_RPE = 9.5


@dataclass(frozen=True)
class Effort:
    """A set's effort, resolved once.

    ``eff_rpe`` is the single RPE-scale figure to use, or None when no effort was reported.
    ``conflict`` means RPE and RIR were both given and disagree by more than the tolerance;
    ``eff_rpe`` then stays the RPE (the dose ladder keeps preferring it), but such a set is
    not admissible as e1RM evidence. ``to_failure`` is an explicit statement or an effort at
    or beyond ``FAILURE_RPE``.
    """

    eff_rpe: float | None
    conflict: bool
    to_failure: bool

    @property
    def known(self) -> bool:
        return self.eff_rpe is not None

    @property
    def admissible(self) -> bool:
        """Effort that can qualify a set as e1RM evidence: reported, and not self-contradictory."""
        return self.eff_rpe is not None and not self.conflict


def resolve_effort(
    rpe: float | None, rir: float | None, explicit_failure: bool = False
) -> Effort:
    """Combine a set's RPE and RIR into one effort.

    * both reported and within ``EFFORT_AGREEMENT_TOLERANCE`` of each other: use the RPE;
    * both reported and further apart: ``conflict`` (the RPE is kept for the dose ladder);
    * one reported: use it (``10 - rir`` when only RIR);
    * neither: unknown (``eff_rpe`` None).
    """
    if rpe is not None and rir is not None:
        gap = abs(float(rpe) - (10.0 - float(rir)))
        conflict = gap > EFFORT_AGREEMENT_TOLERANCE + _EFFORT_ROUNDING_SLACK
        eff: float | None = float(rpe)
    elif rpe is not None:
        conflict, eff = False, float(rpe)
    elif rir is not None:
        conflict, eff = False, 10.0 - float(rir)
    else:
        conflict, eff = False, None
    failure = bool(explicit_failure) or (eff is not None and eff >= FAILURE_RPE)
    return Effort(eff_rpe=eff, conflict=conflict, to_failure=failure)


# What an UNRECOGNIZED fidelity string earns: the least trust any known rung earns.
# Fail-closed — unproven provenance can never inherit set_level authority by default.
MOST_CONSERVATIVE_CONF_MULTIPLIER: float = min(FIDELITY_CONF_MULTIPLIER.values())

# The conservative sentinel used as the signature default wherever effort fidelity is
# unstated. Callers that have *proven* per-set effort must say so explicitly.
FIDELITY_UNSTATED = "missing"

# Load types whose sets carry an external barbell-style load, so a top set can yield
# an e1RM (and a relative-load intensity). Bodyweight / distance / time never do.
LOADED_LOAD_TYPES: frozenset[str] = frozenset(
    {"barbell", "dumbbell", "kettlebell", "machine", "cable"}
)

# ---------------------------------------------------------------------------------
# RPE/RIR → %1RM chart (RTS / Helms shaped). Rows are reps (1..12); columns are RPE
# in half-steps (10.0 down to 6.0). RIR maps to RPE via RPE = 10 - RIR, so RIR 2 ≡
# RPE 8. Values are fractions of 1RM. Anchored to the ADR examples: 5 @ RPE8 ≈ 0.81,
# 5 @ RPE10 ≈ 0.86, a single @ RPE10 = 1.00.
# ---------------------------------------------------------------------------------
_CHART_RPE_STEPS: tuple[float, ...] = (6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0)
_CHART_MIN_REPS = 1
_CHART_MAX_REPS = 12

# reps -> (%1RM at 6.0, 6.5, ... 10.0)
_RPE_CHART: dict[int, tuple[float, ...]] = {
    1:  (0.863, 0.878, 0.892, 0.907, 0.922, 0.939, 0.955, 0.978, 1.000),
    2:  (0.837, 0.850, 0.863, 0.878, 0.892, 0.907, 0.922, 0.939, 0.955),
    3:  (0.811, 0.824, 0.837, 0.850, 0.863, 0.878, 0.892, 0.907, 0.922),
    4:  (0.786, 0.799, 0.811, 0.824, 0.837, 0.850, 0.863, 0.878, 0.892),
    5:  (0.762, 0.774, 0.786, 0.799, 0.811, 0.824, 0.837, 0.850, 0.863),
    6:  (0.739, 0.751, 0.762, 0.774, 0.786, 0.799, 0.811, 0.824, 0.837),
    7:  (0.707, 0.723, 0.739, 0.751, 0.762, 0.774, 0.786, 0.799, 0.811),
    8:  (0.680, 0.694, 0.707, 0.723, 0.739, 0.751, 0.762, 0.774, 0.786),
    9:  (0.653, 0.667, 0.680, 0.694, 0.707, 0.723, 0.739, 0.751, 0.762),
    10: (0.626, 0.640, 0.653, 0.667, 0.680, 0.694, 0.707, 0.723, 0.739),
    11: (0.600, 0.613, 0.626, 0.640, 0.653, 0.667, 0.680, 0.694, 0.707),
    12: (0.574, 0.587, 0.600, 0.613, 0.626, 0.640, 0.653, 0.667, 0.680),
}


@dataclass(frozen=True)
class CalibrationResult:
    """A %1RM / external-intensity value plus the provenance that produced it.

    ``value`` is a fraction of 1RM (or, for dose intensity, ``load / e1RM``). It is
    never a bare float: ``source`` names the ladder rung, ``confidence`` reflects the
    rung and the effort fidelity, and ``model_version`` pins the calibration.
    ``e1rm_pre`` is carried through for the dose provenance (the denominator used).
    """

    value: float
    source: str
    confidence: float
    model_version: str = MODEL_VERSION
    e1rm_pre: float | None = None


# ---------------------------------------------------------------------------------
# Low-level, load-type + rounding utilities (relocated here from the retired
# ``app.logic.e1rm`` so all strength math has one home).
# ---------------------------------------------------------------------------------

def is_loaded(load_type: str | None) -> bool:
    """True when a set of this load_type carries an external load in kg."""
    return load_type in LOADED_LOAD_TYPES


def round_to_increment(kg: float, increment: float = 2.5) -> float:
    """Round a load to the nearest plate increment (default 2.5 kg)."""
    inc = increment if increment > 0 else 2.5
    return round(kg / inc) * inc


# ---------------------------------------------------------------------------------
# Internal fallback models (formerly the three ad-hoc Epley sites). Not called
# directly by consumers — they go through the public ladder functions below.
# ---------------------------------------------------------------------------------

def epley_e1rm(load_kg: float, reps: float) -> float:
    """Estimated 1RM from a ``load_kg × reps`` set (Epley, reps-beyond-first form).

    ``e1RM = load × (1 + (reps - 1) / 30)`` — a true single (``reps = 1``) is its own
    1RM rather than inflated, so an extracted e1RM never overstates a heavy top set.
    Inverse of :func:`_epley_percent`.
    """
    r = max(1.0, float(reps))
    return float(load_kg) * (1.0 + (r - 1.0) / 30.0)


def e1rm_from_set(load_kg: float, reps: float) -> float:
    """The stored e1RM of a set-derived strength observation, rounded to 0.1 kg.

    The one estimate every route that records set-derived evidence uses — workout
    extraction and a set an athlete reports in Assess — so the entry route can never
    change the number a set produces.
    """
    return round(epley_e1rm(load_kg, reps), 1)


# ---------------------------------------------------------------------------------
# The e1RM a set stands for (ADR-0056, P4-2b). ONE function for every route that records set-derived
# evidence (workout extraction, a set reported in Assess, the onboarding seed), so the entry route
# can never change the number a set produces.
# ---------------------------------------------------------------------------------

FORMULA_EPLEY = "epley"
FORMULA_CHART = "rpe_rir_chart"

# The chart's domain. Outside it the lookup clamps, which would put a number on a set the chart
# says nothing about; such a set falls back to the legacy estimate.
CHART_MAX_REPS = _CHART_MAX_REPS
CHART_MIN_EFFORT = _CHART_RPE_STEPS[0]
CHART_MAX_EFFORT = _CHART_RPE_STEPS[-1]


@dataclass(frozen=True)
class E1rmEstimate:
    """A set's e1RM and where it came from. ``model_version`` is None for the legacy estimate,
    which carried none; a chart estimate always carries the chart's."""

    value: float
    formula: str
    model_version: str | None

    @property
    def modeled(self) -> bool:
        return self.formula == FORMULA_CHART


def chart_e1rm_unrounded(load_kg: float, reps: float, eff_rpe: float) -> float:
    """``load / %1RM`` straight from the chart: the exact inverse of the prescription lookup."""
    return float(load_kg) / _chart_percent(reps, eff_rpe)


def estimate_e1rm(
    *, load_kg: float, reps: float, rpe: float | None, rir: float | None, use_chart: bool
) -> E1rmEstimate:
    """The e1RM of one set.

    With ``use_chart``: when the effort is known, self-consistent (``resolve_effort``) and inside
    the chart's domain, ``load / chart(reps, effort)`` rounded to 0.1 kg, formula ``rpe_rir_chart``.
    Anything else (flag off, unknown or contradicting effort, a set outside the chart) is the legacy
    Epley estimate, exactly as before. It never reads a pre-log e1RM: inverting the dose ladder's
    ``load / e1rm_pre`` rung would hand back the estimate it started from.
    """
    if use_chart:
        effort = resolve_effort(rpe, rir)
        if (
            effort.admissible
            and effort.eff_rpe is not None
            and 1 <= reps <= CHART_MAX_REPS
            and CHART_MIN_EFFORT <= effort.eff_rpe <= CHART_MAX_EFFORT
        ):
            return E1rmEstimate(
                value=round(chart_e1rm_unrounded(load_kg, reps, effort.eff_rpe), 1),
                formula=FORMULA_CHART,
                model_version=MODEL_VERSION,
            )
    return E1rmEstimate(value=e1rm_from_set(load_kg, reps), formula=FORMULA_EPLEY, model_version=None)


def _epley_percent(reps: float, rir: float) -> float:
    """Reps-beyond-first Epley %1RM: ``1 / (1 + (reps + rir - 1) / 30)``.

    A single to failure (``reps=1, rir=0``) is 100%, self-consistent with
    :func:`epley_e1rm`. Only meaningful when the set is to-failure / AMRAP.
    """
    reps_to_failure = max(1.0, float(reps) + max(0.0, float(rir)))
    return 1.0 / (1.0 + (reps_to_failure - 1.0) / 30.0)


def _chart_percent(reps: float, rpe: float) -> float:
    """Bilinear lookup of ``%1RM`` from the RPE chart, clamped to the table bounds.

    ``reps`` and ``rpe`` are clamped into the chart's range, then interpolated on
    both axes so fractional reps (e.g. a mean across sets) and off-grid RPE resolve
    smoothly.
    """
    r = min(float(_CHART_MAX_REPS), max(float(_CHART_MIN_REPS), float(reps)))
    e = min(_CHART_RPE_STEPS[-1], max(_CHART_RPE_STEPS[0], float(rpe)))

    r_lo = int(r)
    r_hi = min(_CHART_MAX_REPS, r_lo + 1)
    r_frac = r - r_lo

    # Locate the surrounding RPE half-steps.
    hi_idx = 0
    while hi_idx < len(_CHART_RPE_STEPS) - 1 and _CHART_RPE_STEPS[hi_idx] < e:
        hi_idx += 1
    lo_idx = max(0, hi_idx - 1)
    e_lo, e_hi = _CHART_RPE_STEPS[lo_idx], _CHART_RPE_STEPS[hi_idx]
    e_frac = 0.0 if e_hi == e_lo else (e - e_lo) / (e_hi - e_lo)

    def _row_at(row_reps: int) -> float:
        row = _RPE_CHART[row_reps]
        return row[lo_idx] + (row[hi_idx] - row[lo_idx]) * e_frac

    v_lo = _row_at(r_lo)
    v_hi = _row_at(r_hi)
    return v_lo + (v_hi - v_lo) * r_frac


def _fidelity_conf(base: float, effort_fidelity: str) -> float:
    """Scale a base rung confidence by effort fidelity, failing CLOSED.

    An unrecognized fidelity resolves to the most conservative multiplier, never the
    full ``set_level`` one — a writer that drifts to a new fidelity label must not
    silently gain authority it has not earned.
    """
    multiplier = FIDELITY_CONF_MULTIPLIER.get(
        effort_fidelity, MOST_CONSERVATIVE_CONF_MULTIPLIER
    )
    return round(base * multiplier, 4)


# ---------------------------------------------------------------------------------
# Public: prescription (%1RM → load)
# ---------------------------------------------------------------------------------

def percent_1rm_for_prescription(
    reps: float, rpe_cap: float | None
) -> CalibrationResult:
    """Fraction of 1RM to prescribe for ``reps`` taken to ``rpe_cap`` (chart primary).

    ``rpe_cap is None`` means "to failure" (RPE 10). Clamped to
    ``[PCT_MIN, PCT_MAX_PRESCRIPTION]`` — never prescribe above 100% e1RM.
    """
    rpe = 10.0 if rpe_cap is None else float(rpe_cap)
    pct = _chart_percent(reps, rpe)
    pct = max(PCT_MIN, min(PCT_MAX_PRESCRIPTION, pct))
    return CalibrationResult(
        value=pct, source=SRC_RPE_RIR_CHART, confidence=_CONF_RPE_RIR_CHART
    )


def suggested_load_kg(
    e1rm: float,
    reps: float,
    rpe_cap: float | None,
    increment: float = 2.5,
) -> float:
    """Suggested working load = ``e1RM × %1RM(reps, rpe_cap)``, plate-rounded."""
    pct = percent_1rm_for_prescription(reps, rpe_cap).value
    return round_to_increment(e1rm * pct, increment)


# ---------------------------------------------------------------------------------
# Public: dose external intensity (the ADR-0039 ladder)
# ---------------------------------------------------------------------------------

def external_intensity_for_set(
    *,
    reps: float | None,
    load_kg: float | None,
    rpe: float | None,
    rir: float | None,
    e1rm_pre: float | None,
    to_failure: bool,
    effort_fidelity: str = FIDELITY_UNSTATED,
) -> CalibrationResult:
    """External intensity ``I`` for one set — load relative to capacity (ADR-0039).

    Walks the model ladder and returns the first rung that has enough signal, always
    labeled with its provenance. A missing denominator/effort is ``I = 1.0`` labeled
    ``neutral_missing`` (zero confidence) — never an unlabeled ``1.0`` that reads as
    "moderate intensity" when it means "unknown".
    """
    # 1. Actual relative load — the highest-fidelity signal for dose.
    if e1rm_pre is not None and e1rm_pre > 0 and load_kg is not None and load_kg > 0:
        pct = max(PCT_MIN, min(PCT_MAX_DOSE, float(load_kg) / float(e1rm_pre)))
        return CalibrationResult(
            value=pct,
            source=SRC_RELATIVE_LOAD,
            confidence=_fidelity_conf(_CONF_RELATIVE_LOAD, effort_fidelity),
            e1rm_pre=float(e1rm_pre),
        )

    # 2. RPE/RIR chart — when effort is known.
    effort = resolve_effort(rpe, rir)
    if reps is not None and effort.eff_rpe is not None:
        pct = max(PCT_MIN, min(PCT_MAX_DOSE, _chart_percent(reps, effort.eff_rpe)))
        return CalibrationResult(
            value=pct,
            source=SRC_RPE_RIR_CHART,
            confidence=_fidelity_conf(_CONF_RPE_RIR_CHART, effort_fidelity),
        )

    # 3. Reps-beyond-first Epley — only when the set is to failure / AMRAP.
    if to_failure and reps is not None:
        pct = max(PCT_MIN, min(PCT_MAX_DOSE, _epley_percent(reps, rir or 0.0)))
        return CalibrationResult(
            value=pct,
            source=SRC_EPLEY_FAILURE,
            confidence=_fidelity_conf(_CONF_EPLEY_FAILURE, effort_fidelity),
        )

    # 5. Neutral / missing — honest unknown, degrades the dose to effort-only.
    return CalibrationResult(
        value=1.0, source=SRC_NEUTRAL_MISSING, confidence=_CONF_NEUTRAL
    )
