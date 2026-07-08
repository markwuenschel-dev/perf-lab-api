"""Canonical wellness signal registry (P8, ADR-0053).

One source of truth mapping **logical signals** (what an athlete recognizes: "sleep",
"stress") to the **metric columns** on ``WellnessSample`` (``sleep_hours`` +
``sleep_quality``). The registry drives coverage, the ``signal_summary`` buckets,
implicit tracking, unknown-today detection, reason codes, and UI labels.

Two deliberately-separate taxonomies:
- The **readiness modifier** (``app.services.readiness_service.wellness_modifier``) works at
  the *metric* grain — it z-scores each column and reports per-metric ``components``.
- The **coverage / honesty layer** (confidence, ``signal_summary``) works at the *logical*
  grain, so "sleep" counts once even though it is backed by two columns.

Categories (ADR-0053):
- ``wellness_readiness``  — subjective readiness self-report (sleep, mood, soreness, stress)
- ``biometric_recovery`` — device-measured recovery signals (hrv, rhr)
- ``safety_symptom``     — safety / tissue-risk observations (pain) — NOT wellness coverage,
  reserved for a later safety phase via ``app.logic.tissue_risk.prior_pain_axes``.

Only signals with ``coverage=True`` participate in the wellness-coverage denominator.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.wellness import WellnessSample


@dataclass(frozen=True)
class LogicalSignal:
    """One user-facing wellness signal and how it maps onto storage."""

    key: str
    label: str
    category: str  # "wellness_readiness" | "biometric_recovery" | "safety_symptom"
    metrics: tuple[str, ...] = field(default_factory=tuple)
    coverage: bool = True
    # A logical signal is "provided" if ANY of its metrics is present (vs. requiring all).
    provided_if_any: bool = True


# Ordered for stable UI + deterministic summaries. Pain is intentionally absent from
# ALL_WELLNESS_SIGNALS below; it is a safety_symptom, not wellness coverage.
WELLNESS_SIGNAL_REGISTRY: dict[str, LogicalSignal] = {
    s.key: s
    for s in (
        LogicalSignal("sleep", "Sleep", "wellness_readiness", ("sleep_hours", "sleep_quality")),
        LogicalSignal("hrv", "HRV", "biometric_recovery", ("hrv_ms",)),
        LogicalSignal("rhr", "Resting HR", "biometric_recovery", ("resting_hr",)),
        LogicalSignal("soreness", "Soreness", "wellness_readiness", ("soreness",)),
        LogicalSignal("mood", "Mood", "wellness_readiness", ("mood",)),
        LogicalSignal("stress", "Stress", "wellness_readiness", ("stress",)),
    )
}

# The logical signals eligible for wellness coverage (all current signals; pain excluded).
ALL_WELLNESS_SIGNALS: tuple[str, ...] = tuple(WELLNESS_SIGNAL_REGISTRY)


def logical_signals() -> tuple[str, ...]:
    """All registered logical signal keys, in display order."""
    return ALL_WELLNESS_SIGNALS


def coverage_signals() -> tuple[str, ...]:
    """Logical signals that participate in the wellness-coverage denominator."""
    return tuple(k for k, s in WELLNESS_SIGNAL_REGISTRY.items() if s.coverage)


def metrics_for(signal: str) -> tuple[str, ...]:
    """The ``WellnessSample`` column names backing a logical signal."""
    return WELLNESS_SIGNAL_REGISTRY[signal].metrics


def signal_from_metric(metric: str) -> str | None:
    """Reverse lookup: which logical signal owns a metric column."""
    for key, sig in WELLNESS_SIGNAL_REGISTRY.items():
        if metric in sig.metrics:
            return key
    return None


def _has_value(source: object, metric: str) -> bool:
    return getattr(source, metric, None) is not None


def signal_provided(source: WellnessSample | dict[str, object], signal: str) -> bool:
    """True if a logical signal is present on a sample (any backing metric, by default)."""
    sig = WELLNESS_SIGNAL_REGISTRY[signal]
    if isinstance(source, dict):
        present = [source.get(m) is not None for m in sig.metrics]
    else:
        present = [_has_value(source, m) for m in sig.metrics]
    return any(present) if sig.provided_if_any else all(present)


def provided_signals(source: WellnessSample | dict[str, object]) -> set[str]:
    """The set of logical coverage-signals present on a sample."""
    return {s for s in coverage_signals() if signal_provided(source, s)}
