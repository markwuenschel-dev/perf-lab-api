"""Estimated-1RM extraction + %e1RM ↔ load resolution (ADR-0045 strength loop).

One coherent intensity law shared by the two ends of the strength loop:

* the **write path** — a logged top set becomes an ``e1RM`` benchmark observation
  (``epley_e1rm``); and
* the **prescribe path** — a prescribed lift's ``%e1RM`` resolves to a suggested kg
  against the athlete's current e1RM (``suggested_load_kg``).

Both reuse the Epley relation the dose engine already uses for external load
(``dose_engine_v0._external_intensity_from_reps``, ADR-0039), so measurement,
prescription, and dose all speak the same intensity language rather than three
divergent 1RM formulas.
"""

from __future__ import annotations

# Load types whose sets carry an external barbell-style load, so a top set can
# yield an e1RM. Bodyweight / distance / time sets never do.
LOADED_LOAD_TYPES: frozenset[str] = frozenset(
    {"barbell", "dumbbell", "kettlebell", "machine", "cable"}
)


def is_loaded(load_type: str | None) -> bool:
    """True when a set of this load_type carries an external load in kg."""
    return load_type in LOADED_LOAD_TYPES


def epley_e1rm(load_kg: float, reps: float) -> float:
    """Estimated 1RM from a ``load_kg × reps`` set (Epley, reps-beyond-first form).

    ``e1RM = load × (1 + (reps - 1) / 30)`` — a true single (``reps = 1``) is its
    own 1RM rather than being inflated, so an extracted e1RM never overstates a
    heavy top set. Reduces to the classic Epley shape for multi-rep sets.
    """
    r = max(1.0, float(reps))
    return float(load_kg) * (1.0 + (r - 1.0) / 30.0)


def percent_1rm(reps: float, rpe_cap: float | None = None) -> float:
    """Fraction of 1RM for ``reps`` taken to an RPE of ``rpe_cap``.

    Inverse of :func:`epley_e1rm`: reps-in-reserve = ``10 - rpe_cap`` (0 when no
    cap, i.e. taken to failure), ``reps_to_failure = reps + RIR`` and
    ``%1RM = 1 / (1 + (reps_to_failure - 1) / 30)`` — a single to failure is 100%.
    Same Epley family as the dose engine's external-load term (ADR-0039). Clamped
    to ``[0.3, 1.0]``.
    """
    reserve = 0.0 if rpe_cap is None else max(0.0, 10.0 - float(rpe_cap))
    reps_to_failure = max(1.0, float(reps) + reserve)
    pct = 1.0 / (1.0 + (reps_to_failure - 1.0) / 30.0)
    return max(0.3, min(1.0, pct))


def round_to_increment(kg: float, increment: float = 2.5) -> float:
    """Round a load to the nearest plate increment (default 2.5 kg)."""
    inc = increment if increment > 0 else 2.5
    return round(kg / inc) * inc


def suggested_load_kg(
    e1rm: float,
    reps: float,
    rpe_cap: float | None,
    increment: float = 2.5,
) -> float:
    """Suggested working load = ``e1RM × %1RM(reps, rpe_cap)``, plate-rounded."""
    return round_to_increment(e1rm * percent_1rm(reps, rpe_cap), increment)
