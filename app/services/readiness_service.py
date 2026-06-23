"""Readiness scalar + wellness ingestion (P5 / PDR-0005, ADR-0026).

This module is the **single home for the readiness combine rule** — the guardrail
from ADR-0026 is that swapping how acute wellness mixes with modeled fatigue must
be a one-file change. Per that ADR we implement the *additive-modifier* rule: the
modeled readiness (``1 - mean_fatigue``) is the anchor, and acute daily wellness
(HRV / sleep / RHR / soreness / mood) nudges it within a bounded band. This
preserves the model signal and is less brittle than a hard cap/override.

The per-signal weights below are provisional and meant to be **calibrated against
real data** (ADR-0026); they live here so calibration is a localized edit.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.state_bridge import unified_from_athlete_row
from app.logic.constraint_engine import overall_readiness
from app.models.athlete_state import AthleteState
from app.models.wellness import WellnessSample
from app.schemas.state import UnifiedStateVector
from app.schemas.wellness import (
    ReadinessComponent,
    ReadinessScore,
    WellnessSampleIn,
    WellnessSampleOut,
)

# --- Combine-rule tuning (ADR-0026; provisional, calibrate against real data) ----

# Window over which a personal baseline is averaged for each signal.
BASELINE_WINDOW_DAYS = 28

# Maximum magnitude acute wellness can move the 0–1 readiness scalar. The model
# stays dominant; wellness only nudges. Calibration knob.
WELLNESS_WEIGHT = 0.15

# Per signal: (direction, default_baseline, norm)
#   direction      +1 = higher is better, -1 = lower is better
#   default_baseline  anchor used when the athlete has no personal history yet
#   norm           change (in signal units) that maps to one full unit of deviation
_SIGNAL_CONFIG: dict[str, tuple[int, float, float]] = {
    "hrv_ms":        (+1, 60.0, 20.0),
    "sleep_hours":   (+1, 8.0,  2.0),
    "sleep_quality": (+1, 85.0, 15.0),
    "resting_hr":    (-1, 55.0, 10.0),
    "soreness":      (-1, 3.0,  3.0),   # 0–10, higher = worse
    "mood":          (+1, 6.0,  3.0),   # 0–10, higher = better
}

_SIGNALS = tuple(_SIGNAL_CONFIG)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# --- Pure combine rule (no DB; unit-tested directly) -----------------------------


def wellness_modifier(
    values: dict[str, float | None],
    baselines: dict[str, float | None],
) -> tuple[float, list[ReadinessComponent]]:
    """Additive readiness modifier in 0–1 units from acute wellness signals.

    Each present signal contributes a direction-signed, clamped deviation from
    its personal baseline (or the configured default anchor). The contributions
    are averaged and scaled by ``WELLNESS_WEIGHT``, so the result is bounded to
    ``[-WELLNESS_WEIGHT, +WELLNESS_WEIGHT]``. Returns ``(modifier, breakdown)``.
    """
    components: list[ReadinessComponent] = []
    total = 0.0
    n = 0
    for sig in _SIGNALS:
        v = values.get(sig)
        if v is None:
            continue
        direction, default_bl, norm = _SIGNAL_CONFIG[sig]
        bl = baselines.get(sig)
        if bl is None:
            bl = default_bl
        contribution = _clamp(direction * (v - bl) / norm, -1.0, 1.0)
        total += contribution
        n += 1
        components.append(
            ReadinessComponent(signal=sig, value=v, baseline=bl, contribution=contribution)
        )
    if n == 0:
        return 0.0, []
    return (total / n) * WELLNESS_WEIGHT, components


def combine_readiness(modeled_0_1: float, modifier: float) -> float:
    """Anchor on the model, apply the bounded wellness nudge, clamp to [0, 1]."""
    return _clamp(modeled_0_1 + modifier, 0.0, 1.0)


# --- DB-backed helpers -----------------------------------------------------------


async def _latest_state(db: AsyncSession, user_id: int) -> UnifiedStateVector | None:
    row = (
        await db.execute(
            select(AthleteState)
            .where(AthleteState.user_id == user_id)
            .order_by(AthleteState.timestamp.desc())
            .limit(1)
        )
    ).scalars().first()
    return unified_from_athlete_row(row) if row else None


async def _latest_wellness(db: AsyncSession, user_id: int) -> WellnessSample | None:
    return (
        await db.execute(
            select(WellnessSample)
            .where(WellnessSample.user_id == user_id)
            .order_by(WellnessSample.date.desc(), WellnessSample.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


async def _baselines(db: AsyncSession, user_id: int, before: date_cls) -> dict[str, float | None]:
    """Mean per signal over prior samples in the trailing window (excludes ``before``)."""
    rows = (
        await db.execute(
            select(WellnessSample).where(
                WellnessSample.user_id == user_id,
                WellnessSample.date < before,
                WellnessSample.date >= before - timedelta(days=BASELINE_WINDOW_DAYS),
            )
        )
    ).scalars().all()
    out: dict[str, float | None] = {}
    for sig in _SIGNALS:
        vals = [getattr(r, sig) for r in rows if getattr(r, sig) is not None]
        out[sig] = sum(vals) / len(vals) if vals else None
    return out


async def upsert_wellness_sample(
    db: AsyncSession, user_id: int, payload: WellnessSampleIn
) -> WellnessSample:
    """Idempotent on (user_id, date, source): update in place, else insert."""
    existing = (
        await db.execute(
            select(WellnessSample).where(
                WellnessSample.user_id == user_id,
                WellnessSample.date == payload.date,
                WellnessSample.source == payload.source,
            )
        )
    ).scalars().first()

    fields = payload.model_dump(exclude={"date", "source"})
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        sample = existing
    else:
        sample = WellnessSample(
            user_id=user_id, date=payload.date, source=payload.source, **fields
        )
        db.add(sample)
    await db.commit()
    await db.refresh(sample)
    return sample


async def list_wellness_samples(
    db: AsyncSession, user_id: int, *, limit: int = 30
) -> list[WellnessSample]:
    return list(
        (
            await db.execute(
                select(WellnessSample)
                .where(WellnessSample.user_id == user_id)
                .order_by(WellnessSample.date.desc(), WellnessSample.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
    )


async def compute_readiness(db: AsyncSession, user_id: int) -> ReadinessScore:
    """The one backend-owned readiness number (PDR-0005)."""
    state = await _latest_state(db, user_id)
    sample = await _latest_wellness(db, user_id)
    sample_out = WellnessSampleOut.model_validate(sample) if sample else None

    if state is None:
        # Wellness modulates the model; with no modeled state there is nothing to
        # anchor, so the scalar is undefined (mirrors GET /dashboard/readiness).
        return ReadinessScore(wellness_sample=sample_out, note="no_modeled_state")

    modeled_0_1 = overall_readiness(state)

    modifier = 0.0
    components: list[ReadinessComponent] = []
    if sample is not None:
        values = {sig: getattr(sample, sig) for sig in _SIGNALS}
        baselines = await _baselines(db, user_id, before=sample.date)
        modifier, components = wellness_modifier(values, baselines)

    readiness_0_1 = combine_readiness(modeled_0_1, modifier)
    return ReadinessScore(
        readiness=round(readiness_0_1 * 100.0, 1),
        modeled=round(modeled_0_1 * 100.0, 1),
        wellness_delta=round(modifier * 100.0, 1),
        components=components,
        wellness_sample=sample_out,
        as_of=state.timestamp,
        note=None if sample is not None else "no_wellness_sample",
    )
