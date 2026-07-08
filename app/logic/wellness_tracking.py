"""Implicit-tracking model for wellness signals (P8, ADR-0053 / ADR-0049).

The durable rule: *missing an expected signal lowers confidence; not owning one does not.*

A logical signal becomes **expected** once the athlete has shown they can provide it
(logged it ≥1 time) or has explicitly opted in; it stops being expected if explicitly
marked untracked. A signal never provided and never opted in is **untracked by default**
— hidden, never expected, never penalized. This is fair to device-less users with zero
configuration and honest after a user has shown they track something.

Explicit opt-in is empty in P8 (the guided "which signals do you track?" onboarding step is
P10); implicit history + explicit opt-out (``AthleteProfile.untracked_wellness_signals``)
suffice for now.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.logic.wellness_registry import coverage_signals


def get_expected_tracked_signals(
    provided_history: Iterable[str],
    explicitly_tracked: Iterable[str] = (),
    explicitly_untracked: Iterable[str] = (),
) -> set[str]:
    """Logical signals the athlete is expected to provide.

    ``expected = (provided ≥1 time ∪ explicitly opted-in) − explicitly untracked``,
    restricted to coverage-eligible signals.
    """
    coverage = set(coverage_signals())
    opted_in = set(provided_history) | set(explicitly_tracked)
    opted_out = set(explicitly_untracked)
    return (opted_in - opted_out) & coverage
