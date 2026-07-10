---
status: accepted
delivery: shadow-only
date: 2026-07-09
---
# Per-exercise dose routing through exercise φ vectors

[ADR-0039](0039-dose-law-external-load-vs-effort.md) closed on **Model A**: a single
session-scalar `external_intensity` enters one session `base`, which `_shape_six` distributes
across the six axes using the **session-aggregate φ** and the **derived session modality**.
That is enough to stop the `I=1` lie, but it cannot answer *where* the stress landed. A hard
biceps curl in a squat session raises the session scalar, then gets shaped by the squat-
dominated aggregate — so its intensity bleeds toward whole-body max-strength axes instead of
local/hypertrophy. Model A over-attributes accessory and mixed-modality work.

Worse, **tissue is already broken and worse than "modality-shaped"**: the dose engine
aggregates `phi_tissue` and then **discards it**; state evolution re-derives tissue φ from
scratch off `default_phi_for_row(log.modality, …)` (`state_update_v0._tissue_impulse_from_dose`).
A hard grip carry in a run session deposits *running* tissue load. Model B fixes this at the root.

## Decision (Model B — landing shadow-only; see "Landing & promotion")

Build the routed dose by **summing per-exercise contributions**, each exercise's dose landing
through **its own** φ vectors, and emit three parallel per-vector contribution objects that a
future state-update promotion will consume in place of the modality-shaped paths:

```
D_i  = base(V_i) · I_i^α · N_i^γ · R_i^ρ           # per-exercise dose (I_i from ADR-0056 + ADR-0055 pre-log e1RM)
raw_adapt^X   = Σ_i φ^adapt_i(X) · D_i             # → capacity axes (via PHI_ADAPT_TO_CAPACITY)
raw_fatigue^F = Σ_i φ^fatigue_i(F) · D_i           # φ_fatigue keys ≡ FatigueState axes (1:1)
raw_tissue^T  = Σ_i φ^tissue_i(T) · D_i            # φ_tissue keys ≡ TissueState axes (1:1)
```

`d_struct_signal` likewise becomes **exercise-derived** — `Σ` over exercises, where an
endurance-dominant exercise contributes ~0 structural signal — replacing the lossy
`if log.modality == "Running"` special-case keyed off the session Literal.

### Compatibility scale — raw model space vs. 0–100 control space

The raw `Σ φ·D` quantities are **model-native and unbounded**; the live engine's deload,
interference, and **safety** thresholds are hand-set literals in a **0–100 fatigue/tissue
space** (`adapt_fatigue_suppress_threshold = 45`; deload hard rules at fatigue axis > 60,
mean > 45, tissue > 55) with **no tuning harness** to re-derive them. Changing the upstream
dose scale *and* those thresholds at once is uncontrolled. So each raw quantity crosses an
explicit **versioned compatibility scale** into control space:

```
raw_fatigue_dose            = Σ_i φ^fatigue_i · D_i                    # raw model space
fatigue_delta_compat_0_100  = k_fatigue_v1 · raw_fatigue_dose          # control space
fatigue_scale_model_version = "fatigue_compat_v1"
```

Rules (non-negotiable):
- **Two spaces are kept distinct.** Raw φ·D is stored for observability / the future harness;
  the compat-scaled value is what any threshold would consume. **Raw φ·D never touches a
  threshold directly.**
- **`k` is chosen by distribution-matching, not vibes.** `k_·_v1 = median(old_delta / raw_dose)`
  over eligible sim-corpus sessions; validated at P50/P75/P90/P95 and against canonical
  easy/moderate/hard/brutal sessions (never one toy session).
- **Separate `k` per vector** (`k_fatigue_v1`, `k_tissue_v1`, `k_adapt_v1`, `k_struct_v1`) —
  φ-weight sums and D distributions differ, so a shared `k` would misalign them.
- **Honest naming.** The scaled value is a *compatibility bridge*, not a validated physiology
  unit; `calibration_basis = "sim_scenario_distribution_match_v1"` records that it is
  **sim-derived** (there is no first-party historical session corpus yet).
- **Do not clip early.** Store `raw_*`, the *unclipped* compat value, and (if a consumer needs
  bounded input) the saturated value — observability needs the unclipped signal.

### Routing basis — two-tier fallback, no λ

No continuous `λ` blend between φ-shaping and modality-shaping (fake precision over what is
really a coverage gap, and an unvalidated knob). Routing is a **coverage/missingness ladder**:

```
if any dose-bearing exercise has resolved φ:
    routing_basis = "exercise_phi"
    resolved exercise   → its own φ vectors
    unresolved exercise → conservative substitute, low confidence,
                          routing_basis="unresolved_exercise_fallback",
                          fallback_reason="missing_exercise_phi"   # dose NEVER erased
else:
    routing_basis = "session_modality_fallback"   # conservative session-modality φ
```

Session modality is **never** blended with resolved exercise φ. Unresolved exercises keep
depositing (conservative) dose, so **total routed dose ≈ session dose regardless of φ
coverage** — which the compat-scale calibration depends on. Missing-φ coverage is emitted as
provenance.

## Landing & promotion (shadow-only this PR)

This PR is **capture-only**, mirroring the EKF/MPC shadow pattern
([ADR-0041](0041-shadow-ekf-covariance.md)/[ADR-0042](0042-shadow-mpc-planner.md)):

1. The dose engine computes the routed vectors + raw/compat/version/provenance as **pure
   functions**; a `dose_routing_shadow_service` persists them to a new `dose_routing_shadow_log`
   (`decision_impact = "none_shadow_only"`). `state_update_v0` is **unchanged** — the old
   modality-shaped paths still drive state, so every state-trajectory / deload / EKF / MPC test
   stays green.
2. `StressDose` / the OpenAPI contract stay **clean**; the shadow lives in its own table
   (queryable for the future tuning harness).
3. A **sim-corpus equivalence test** proves the compat scale preserves the control regime:
   Model B compat-scaled fatigue/tissue trigger rates ≈ Model A within tolerance. A
   **calibration-reproducibility test** re-derives each `k_·_v1` from the sim corpus and
   asserts the frozen constant matches.
4. **Promotion** (flipping `state_update` to consume the contribution vectors) is a **later
   PR**, gated on a real post-launch old-vs-new safety-outcome comparison (deload trigger rate,
   safety-deload rate, interference-warning rate, threshold crossings). If trigger rates shift
   materially, adjust `k` or keep shadowing — **thresholds are not re-tuned** until a real
   tuning/evaluation harness exists. That is a separate later project.

## Consequences

- Fixes the tissue-off-session-label defect at the root; accessory / mixed-modality intensity
  no longer bleeds through the session-aggregate shape.
- Adds `dose_routing_shadow_log` (migration) + `dose_routing_shadow_service` + pure routing
  math in the dose engine + the versioned compat calibrator. No `state_update`, threshold, or
  public-schema change this PR.
- Session-scalar intensity (Model A) is retained as summary metadata; per-exercise axis
  contributions live in the shadow row.
- Composes with (does not promote) the shadow EKF/MPC.
- **Deferred:** state-update promotion; threshold re-tuning; a real tuning/evaluation harness;
  a `fatigue_compat_v1 → …v2 → learned/tuned_threshold_v1` migration path.
