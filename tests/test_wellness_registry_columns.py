"""Pins WELLNESS_SIGNAL_REGISTRY's metric strings to real WellnessSample columns.

The registry hardcodes DB column names as strings and reads them back with
``getattr(source, metric, None)``. That ``None`` default means a renamed or removed
column does not raise — the signal is silently reported as NOT PROVIDED, and that
fake-but-plausible gap flows into coverage and confidence looking like an honest one.
CONTEXT.md's "Wellness signal state" contract says a gap must be real ("missing is
never silently imputed"); a rename manufacturing a gap violates it just as badly as
imputation would.

These checks are DB-free: they introspect the SQLAlchemy mapper, never connect.
"""

from sqlalchemy import inspect

from app.logic.wellness_registry import WELLNESS_SIGNAL_REGISTRY
from app.models.wellness import WellnessSample


def _wellness_sample_columns() -> set[str]:
    """Mapped column names on WellnessSample, via the mapper (no DB connection).

    Deliberately not ``hasattr``: a declarative class answers truthily to plenty of
    names that are not mapped columns, so it cannot catch the rename this guards.
    """
    return set(inspect(WellnessSample).columns.keys())


def test_every_registry_metric_is_a_real_wellness_sample_column():
    columns = _wellness_sample_columns()
    unknown = {
        (signal.key, metric)
        for signal in WELLNESS_SIGNAL_REGISTRY.values()
        for metric in signal.metrics
        if metric not in columns
    }
    assert not unknown, (
        "WELLNESS_SIGNAL_REGISTRY references metrics that are not mapped columns on "
        f"WellnessSample: {sorted(unknown)}. A renamed/removed column would otherwise "
        "silently report the signal as unknown-today, manufacturing a fake coverage gap. "
        f"Mapped columns: {sorted(columns)}"
    )


def test_every_registry_signal_has_at_least_one_metric():
    """A signal with no metrics can never be provided — it is a permanent silent gap."""
    empty = [key for key, sig in WELLNESS_SIGNAL_REGISTRY.items() if not sig.metrics]
    assert not empty, f"Logical signals with no backing metric: {empty}"


def test_no_metric_is_claimed_by_two_logical_signals():
    """signal_from_metric() is a reverse lookup; a shared metric makes it ambiguous."""
    seen: dict[str, str] = {}
    clashes: list[tuple[str, str, str]] = []
    for key, sig in WELLNESS_SIGNAL_REGISTRY.items():
        for metric in sig.metrics:
            if metric in seen:
                clashes.append((metric, seen[metric], key))
            seen[metric] = key
    assert not clashes, f"Metrics claimed by more than one logical signal: {clashes}"


def test_registry_covers_the_known_wellness_metrics():
    """Guards the other direction: a NEW wellness column added to the model without a
    registry entry is invisible to coverage. Non-signal columns are excluded explicitly,
    so adding one forces a deliberate choice here rather than a silent omission."""
    non_signal = {"id", "user_id", "date", "source", "raw", "created_at"}
    unregistered = _wellness_sample_columns() - non_signal - {
        metric for sig in WELLNESS_SIGNAL_REGISTRY.values() for metric in sig.metrics
    }
    assert not unregistered, (
        f"WellnessSample columns absent from the registry and from the non-signal "
        f"allowlist: {sorted(unregistered)}. Either register the signal or add the "
        "column to `non_signal` deliberately."
    )
