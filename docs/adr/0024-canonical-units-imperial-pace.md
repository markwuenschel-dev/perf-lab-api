---
status: accepted
date: 2026-06-20
---
# Canonical units: sec/mile pace, 0–100 fatigue/tissue

Canonical unit choices for stored and API-exposed values:

- **Pace is stored as seconds per mile.** This is surprising — most engineers reach
  for SI (sec/km or m/s) — but Perf Lab's heritage and primary user are US tactical
  running (the `300m + 1.5-mile` field test, the `/compute-metrics` calculators), and
  keeping pace imperial avoids a conversion seam through the legacy calculators.
  Conversion to /km happens at the display edge for non-US users.
- **Fatigue (`F`) and tissue (`T`) are on a 0–100 scale** (already enforced in
  `app/domain/vectors.py` via `le=100.0`). Capacity (`X`) stays in unbounded
  engineering units (e.g. aerobic defaults to ~300). UI normalization to 0–1 or % is a
  display concern only.

We considered storing SI base quantities (meters + seconds) and deriving pace at the
edge; rejected for now to stay aligned with the existing imperial calculators.

**Guardrail:** do not "fix" stored pace to SI or rescale `F`/`T` off 0–100 without a
migration and superseding ADR — downstream formulas and seeded benchmarks assume these
units.
