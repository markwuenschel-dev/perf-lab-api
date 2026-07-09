---
status: proposed
date: 2026-07-09
---
# Per-exercise dose routing through exercise φ vectors

[ADR-0039](0039-dose-law-external-load-vs-effort.md) closes on **Model A**: a single
session-scalar `external_intensity` enters one session `base`, which `_shape_six` distributes
across the six axes using the **session-aggregate φ** and the **derived session modality**.
That is enough to stop the `I=1` lie, but it cannot answer *where* the stress landed. A hard
biceps curl in a squat session raises the session scalar, then gets shaped by the squat-
dominated aggregate — so its intensity bleeds toward whole-body max-strength axes instead of
local/hypertrophy. Model A over-attributes accessory and mixed-modality work.

## Decision (target engine — not yet built)

Build the six-axis dose by **summing per-exercise routed contributions**, each exercise's
intensity landing through **its own** φ vectors:

```
D_i  = base(V_i) · I_i^α · N_i^γ · R_i^ρ           # per-exercise dose
D^X  = Σ_i φ^X_i · D_i                              # adaptation axes
D^F  = Σ_i φ^F_i · D_i                              # fatigue axes
D^T  = Σ_i φ^T_i · D_i                              # tissue axes
```

Consequences of adopting it:
1. The engine builds per-exercise dose objects (revives the currently-discarded
   `_build_exercise_doses` `base`/`I_i`).
2. Each exercise computes its own `D_i` and `I_i` (via the [ADR-0056](0056-canonical-percent-1rm-calibration.md)
   calibration + [ADR-0055](0055-strength-evidence-ledger.md) pre-log e1RM).
3. Adaptation / fatigue / tissue route through per-exercise φ, not session-aggregate φ.
4. Session six-axis dose = sum of routed per-exercise contributions.
5. **Session modality stops being a dose router** — it degrades to a display / filtering /
   planning summary and a fallback for logs without resolved exercise data. The derived-
   modality collapse and the Running-special-cased `d_struct_signal` both become exercise-
   derived rather than session-label-derived.
6. Dose explanation shows per-exercise axis contributions.

**Interim bridge (may ship inside Model A):** shape from φ first and use the session modality
only as a weak, provenance-labeled prior: `S = (1−λ)·S_φ + λ·S_modality`, with `λ` small (→ 0
when φ is high-confidence, ~0.05 for Mixed, higher only when φ is sparse). And the Running
structural signal should already be exercise-derived (`Σ over exercises where modality ==
Running`), not keyed off the lossy session Literal.

## Consequences

- Larger engine change than ADR-0039: per-exercise dose objects, φ routing, six-axis
  aggregation, tissue/adaptation/fatigue routing, dose explanations, fixtures, possibly the
  stored `dose_snapshot` schema. Hence a separate ADR and a separate PR — **not** folded into
  ADR-0039.
- Acceptance: accessory intensity no longer bleeds through the session aggregate shape;
  session-scalar intensity is retained only as summary metadata; per-exercise axis
  contributions are in the explanation.
- Composes with (does not promote) the shadow EKF/MPC ([ADR-0041]/[ADR-0042]).
