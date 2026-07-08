---
status: accepted
date: 2026-07-08
---
# Canonical wellness signal registry, categories, and implicit tracking

P8 needs one source of truth for "what is a wellness signal" across coverage math, the
`signal_summary` buckets, implicit tracking, reason codes, and UI labels. Hand-mirroring that
across backend and frontend would drift.

## Registry + logical-vs-metric taxonomy

`app/logic/wellness_registry.py` maps **logical signals** (what an athlete recognizes) to the
**metric columns** on `WellnessSample`:

| logical | metrics | category | coverage |
|---|---|---|---|
| sleep | `sleep_hours`, `sleep_quality` | wellness_readiness | ✓ |
| soreness | `soreness` | wellness_readiness | ✓ |
| mood | `mood` | wellness_readiness | ✓ |
| stress | `stress` | wellness_readiness | ✓ |
| hrv | `hrv_ms` | biometric_recovery | ✓ |
| rhr | `resting_hr` | biometric_recovery | ✓ |

Two deliberately-separate grains: the **readiness modifier** z-scores each *metric* (its
`components` stay metric-level); the **honesty layer** (coverage, `signal_summary`, confidence)
works at the *logical* grain, so `sleep` counts once though two columns back it (`provided` if
either is present — no double-count). The frontend mirrors this in `web/src/perflab/wellnessSignals.ts`.

## Categories

`wellness_readiness` (subjective), `biometric_recovery` (device), `safety_symptom` (safety/
tissue-risk). Only signals flagged `coverage=true` enter the wellness-coverage denominator.

## Add `stress` now

Stress is core subjective-readiness data — same family as mood/soreness/sleep — device-free, and
fits implicit tracking. Migration `a023_add_wellness_stress` adds the nullable column; a conservative
`SIGNAL_CONFIG` entry (`-1, 4.0, 3.0`, 0–10 higher-is-worse) keeps it from dominating. Shipping it
in P8 avoids re-baselining coverage denominators and signal buckets after users start logging.

## Pain is not a wellness signal

Pain answers a different question (is there a safety/tissue-risk constraint?), so it is
`safety_symptom` — excluded from `ALL_WELLNESS_SIGNALS`, coverage, and `signal_summary`. It is
deferred entirely from P8; its future home is already reserved on both ends: the
`app/logic/tissue_risk.py::prior_pain_axes` hook (present but currently fed by nothing) and a
dedicated `PainCheckin` shape in a later safety phase.

## Implicit tracking + explicit override

`get_expected_tracked_signals(provided_history, explicitly_tracked, explicitly_untracked)`:
a signal is *expected* once provided ≥1 time or explicitly opted in, and stops being expected if
explicitly marked untracked (`AthleteProfile.untracked_wellness_signals`). Never-provided +
never-opted-in = untracked-by-default — hidden, no penalty. Explicit opt-in is empty in P8 (the
guided "which signals do you track?" onboarding step is P10); implicit history + opt-out suffice.

**Guardrail:** missing an *expected* signal lowers confidence; not owning a signal never does. See
[ADR-0049](0049-missing-wellness-is-a-gap-not-imputed.md) and
[ADR-0052](0052-readiness-confidence-report-only.md).
