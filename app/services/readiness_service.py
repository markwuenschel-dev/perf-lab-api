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

from datetime import UTC, datetime, timedelta
from datetime import date as date_cls

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.constraint_engine import overall_readiness
from app.logic.wellness_registry import (
    WELLNESS_SIGNAL_REGISTRY,
    coverage_signals,
    metrics_for,
    provided_signals,
    signal_from_metric,
)
from app.logic.wellness_signals import SIGNAL_CONFIG as _SIGNAL_CONFIG
from app.logic.wellness_tracking import get_expected_tracked_signals
from app.models.user import AthleteProfile
from app.models.wellness import WellnessSample
from app.schemas.wellness import (
    ConfidenceBand,
    ReadinessBand,
    ReadinessComponent,
    ReadinessConfidence,
    ReadinessScore,
    RecommendationAuthority,
    RecommendationGate,
    SignalSummary,
    WellnessSampleIn,
    WellnessSampleOut,
)
from app.services.state_service import load_current_state

# --- Confidence tuning (P8; ADR-0052; provisional, calibrate) --------------------

# Evidence-coverage weights: how much load / wellness-coverage / freshness / baseline
# maturity each contribute to the confidence scalar. If wellness is not-applicable
# (the athlete tracks no coverage signals) its weight is dropped and the rest renormalize.
CONF_W_LOAD = 0.35
CONF_W_WELLNESS = 0.35
CONF_W_FRESHNESS = 0.20
CONF_W_BASELINE = 0.10

# Distinct sample-days before personal baselines are considered mature.
BASELINE_MATURITY_DAYS = 14

# Confidence band cutoffs.
CONF_BAND_HIGH = 0.75
CONF_BAND_MEDIUM = 0.45

# Readiness (0–100) band cutoffs.
READINESS_BAND_CUTOFFS: tuple[tuple[float, ReadinessBand], ...] = (
    (75.0, "high"),
    (60.0, "good"),
    (40.0, "moderate"),
)

# --- Combine-rule tuning (ADR-0026; provisional, calibrate against real data) ----

# Window over which a personal baseline is averaged for each signal.
BASELINE_WINDOW_DAYS = 28

# Maximum magnitude acute wellness can move the 0–1 readiness scalar. The model
# stays dominant; wellness only nudges. Calibration knob.
WELLNESS_WEIGHT = 0.15

# Per-signal z-score config (direction, default_baseline, norm) now lives in the shared
# app.logic.wellness_signals.SIGNAL_CONFIG so the recovery-clearance telemetry can't drift
# from it. Imported above as _SIGNAL_CONFIG; the readiness combine rule stays the one home.
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


def readiness_band(score_0_100: float) -> ReadinessBand:
    """Coarse UI band over the 0–100 readiness score."""
    for cutoff, band in READINESS_BAND_CUTOFFS:
        if score_0_100 >= cutoff:
            return band
    return "low"


def confidence_band(conf_0_1: float) -> ConfidenceBand:
    return "high" if conf_0_1 >= CONF_BAND_HIGH else "medium" if conf_0_1 >= CONF_BAND_MEDIUM else "low"


def _authority_for(band: ConfidenceBand, has_expected: bool) -> RecommendationAuthority:
    """Map confidence band → advisory recommendation authority (report-only in P8)."""
    if band == "high":
        return "normal"
    if band == "medium":
        return "conservative"
    # low confidence: if the athlete tracks nothing to improve it, prompt an assessment;
    # otherwise stay very conservative. Never blocks (PDR-0010) — advisory only.
    return "very_conservative" if has_expected else "assessment_prompt_only"


def build_confidence(
    *,
    has_load_model: bool,
    expected: set[str],
    provided_today: set[str],
    fresh: bool,
    stale_signals: set[str],
    baseline_days: int,
    untracked: set[str],
) -> ReadinessConfidence:
    """Assemble the evidence-coverage confidence object (ADR-0052).

    Pure given its inputs so it can be unit-tested directly. Wellness coverage is
    *not-applicable* (its weight dropped, the rest renormalized) when the athlete tracks
    no coverage signals — never treated as full coverage (no all-untracked loophole).
    """
    c_load = 1.0 if has_load_model else 0.0
    c_freshness = 1.0 if fresh else 0.0
    c_baseline = _clamp(baseline_days / BASELINE_MATURITY_DAYS, 0.0, 1.0)

    terms: list[tuple[float, float]] = [
        (CONF_W_LOAD, c_load),
        (CONF_W_FRESHNESS, c_freshness),
        (CONF_W_BASELINE, c_baseline),
    ]
    wellness_applicable = len(expected) > 0
    if wellness_applicable:
        c_wellness = len(provided_today & expected) / len(expected)
        terms.append((CONF_W_WELLNESS, c_wellness))

    weight_sum = sum(w for w, _ in terms)
    score = sum(w * v for w, v in terms) / weight_sum if weight_sum else 0.0
    band = confidence_band(score)

    # --- status ---
    if stale_signals and not provided_today:
        status = "stale_data"
    elif not wellness_applicable or (not provided_today and not fresh):
        status = "sparse_data"
    elif expected and provided_today >= expected:
        status = "well_supported"
    else:
        status = "partial_data"

    # --- machine-readable reasons ---
    reasons: list[str] = [
        "training_load_model_available" if has_load_model else "training_load_model_sparse",
        "checkin_fresh" if fresh else "checkin_stale",
        "baseline_mature" if baseline_days >= BASELINE_MATURITY_DAYS else "baseline_immature",
    ]
    for sig in coverage_signals():
        if sig in provided_today:
            reasons.append(f"{sig}_provided")
        elif sig in (expected - provided_today):
            reasons.append(f"{sig}_unknown_today")
        elif sig in untracked:
            reasons.append(f"{sig}_untracked")

    authority = _authority_for(band, has_expected=wellness_applicable)
    gate = RecommendationGate(
        max_recommendation_authority=authority,
        message=_gate_message(authority, expected - provided_today, stale_signals),
        enforced=False,  # report-only in P8 — ADR-0052
    )

    return ReadinessConfidence(
        score=round(score, 3),
        band=band,
        status=status,
        reasons=reasons,
        signal_summary=SignalSummary(
            provided=sorted(provided_today),
            unknown_today=sorted(expected - provided_today),
            untracked=sorted(untracked),
            stale=sorted(stale_signals),
            estimated=[],  # no carry-forward in P8 (ADR-0049)
        ),
        recommendation_gate=gate,
    )


def _label(sig: str) -> str:
    reg = WELLNESS_SIGNAL_REGISTRY.get(sig)
    return reg.label if reg else sig


def _gate_message(
    authority: RecommendationAuthority, missing: set[str], stale_signals: set[str]
) -> str | None:
    """Human-friendly, honest copy. Describes the DATA situation, since the gate is not
    enforced in P8 — it never claims an effect the prescriber does not apply."""
    if authority == "normal":
        return None
    if stale_signals:
        return "Today's check-in is missing, so readiness leans on your training model."
    if authority == "assessment_prompt_only":
        return "We have limited information today. Add sleep, soreness, or HRV to improve confidence."
    if missing:
        names = ", ".join(_label(s) for s in sorted(missing))
        return f"Readiness is calculated without {names} today, so confidence is lower."
    return "Readiness is based on limited data today, so confidence is lower."


# --- DB-backed helpers -----------------------------------------------------------


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


async def _provided_history_signals(db: AsyncSession, user_id: int) -> set[str]:
    """Logical coverage-signals the athlete has provided at least once (implicit tracking)."""
    metrics = [m for sig in coverage_signals() for m in metrics_for(sig)]
    cols = [func.count(getattr(WellnessSample, m)).label(m) for m in metrics]
    row = (await db.execute(select(*cols).where(WellnessSample.user_id == user_id))).one()
    present = {m for m in metrics if getattr(row, m) > 0}
    return {sig for m in present if (sig := signal_from_metric(m)) is not None}


async def _untracked_signals(db: AsyncSession, user_id: int) -> set[str]:
    """The athlete's explicit 'I don't track this' opt-outs (ADR-0049)."""
    raw = (
        await db.execute(
            select(AthleteProfile.untracked_wellness_signals).where(
                AthleteProfile.user_id == user_id
            )
        )
    ).scalars().first()
    return set(raw or []) & set(coverage_signals())


async def _baseline_days(db: AsyncSession, user_id: int) -> int:
    """Count of distinct sample-days (proxy for personal-baseline maturity)."""
    return int(
        (
            await db.execute(
                select(func.count(func.distinct(WellnessSample.date))).where(
                    WellnessSample.user_id == user_id
                )
            )
        ).scalar_one()
    )


async def compute_readiness(
    db: AsyncSession, user_id: int, *, today: date_cls | None = None
) -> ReadinessScore:
    """The one backend-owned readiness number (PDR-0005) + its confidence (ADR-0052).

    Only a *fresh* (today's) sample feeds the readiness modifier — a stale sample is never
    used as if current (ADR-0049: no silent carry-forward); it lowers freshness confidence
    and is surfaced in ``signal_summary.stale`` instead.
    """
    today = today or datetime.now(UTC).date()
    state = await load_current_state(db, user_id)
    latest = await _latest_wellness(db, user_id)
    latest_out = WellnessSampleOut.model_validate(latest) if latest else None

    explicit_untracked = await _untracked_signals(db, user_id)
    history = await _provided_history_signals(db, user_id)
    expected = get_expected_tracked_signals(history, explicitly_untracked=explicit_untracked)
    baseline_days = await _baseline_days(db, user_id)

    fresh_sample = latest if latest is not None and latest.date >= today else None
    stale_sample = latest if latest is not None and latest.date < today else None
    provided_today = provided_signals(fresh_sample) if fresh_sample is not None else set[str]()
    stale_signals = provided_signals(stale_sample) if stale_sample is not None else set[str]()
    untracked_bucket = set(coverage_signals()) - expected

    def _confidence(has_load: bool) -> ReadinessConfidence:
        return build_confidence(
            has_load_model=has_load,
            expected=expected,
            provided_today=provided_today,
            fresh=fresh_sample is not None,
            stale_signals=stale_signals,
            baseline_days=baseline_days,
            untracked=untracked_bucket,
        )

    if state is None:
        # Wellness modulates the model; with no modeled state there is nothing to anchor.
        return ReadinessScore(
            confidence=_confidence(has_load=False),
            wellness_sample=latest_out,
            note="no_modeled_state",
        )

    modeled_0_1 = overall_readiness(state)
    modifier = 0.0
    components: list[ReadinessComponent] = []
    if fresh_sample is not None:
        values = {sig: getattr(fresh_sample, sig) for sig in _SIGNALS}
        baselines = await _baselines(db, user_id, before=fresh_sample.date)
        modifier, components = wellness_modifier(values, baselines)

    readiness_0_1 = combine_readiness(modeled_0_1, modifier)
    score_0_100 = round(readiness_0_1 * 100.0, 1)
    return ReadinessScore(
        score=score_0_100,
        band=readiness_band(score_0_100),
        modeled=round(modeled_0_1 * 100.0, 1),
        wellness_delta=round(modifier * 100.0, 1),
        components=components,
        confidence=_confidence(has_load=True),
        wellness_sample=latest_out,
        as_of=state.timestamp,
        note=(
            None
            if fresh_sample is not None
            else ("stale_wellness_sample" if stale_sample is not None else "no_wellness_sample")
        ),
    )


async def combined_readiness_scalar(
    db: AsyncSession, user_id: int, *, today: date_cls | None = None
) -> float | None:
    """The freshness-respecting combined readiness in 0–1 for the prescriber (ADR-0052).

    This is the *score* channel only — the prescriber may let it transparently nudge candidate
    scoring (bounded by ``WELLNESS_WEIGHT``). Confidence is NOT part of this and must never gate.
    """
    rs = await compute_readiness(db, user_id, today=today)
    return None if rs.score is None else rs.score / 100.0
