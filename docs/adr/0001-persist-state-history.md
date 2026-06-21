---
status: accepted
date: 2026-06-20
---
# Persist state history, not just the latest state

Athlete state is stored as an append-only time series of `AthleteState` rows because
Performance Lab models an *evolving* system — the sequence of states matters, not only
the latest snapshot. This buys auditability, trend visibility, replay, easier
debugging, and model-version migration. We considered keeping a single mutable
current-state row per athlete and rejected it: that hides how the model changed over
time and makes replay after logic changes much harder.

**Guardrail:** do not overwrite prior state rows as the default update pattern.
