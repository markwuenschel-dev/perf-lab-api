# Phase 8: calibration, built through "ready to fit"

**Outcome:** the fit was NOT RUN, validation was NOT RUN, and v1 is still OFF in production.

Phase 8 was meant to calibrate dose model v1 so it could replace v0:

| Step | What it is | Status |
|---|---|---|
| 8A | capture v1 beside v0 | done |
| 8B | fit on real logged data | not run |
| 8C | validate on held-out athletes, then activate | not run |

8B depends on data that does not exist yet. So this phase built everything up to the fit,
made accidental activation impossible, and stopped. That stop was the plan's stated outcome
for thin data (fork F19 = a), decided before the work started.

## Why 8B was not run: the census of 2026-09-27

Run read-only on EC2 before any phase-8 code. It was one `READ ONLY` transaction inside the
`perf-lab-api` container against the production database, and nothing was written.

| | |
|---|---|
| users | 2 (0 seeded `@perflab.local`) |
| workout logs | 1 (2026-09-20) |
| `dose_model_shadow_log` rows | 1, from 1 athlete |
| that row | Strength, unplanned, `v1_model_version` "v1", basis `sets_per_elapsed_minute`, fit-eligible |
| labelled next-session pairs (1–4 days) | **0** |
| athletes with ≥ 2 eligible sessions | 0 |
| athletes who could be held out | 0 |
| null rate, `planned_domain` / `planned_category` | 1/1 |
| null rate, `code_version` | 1/1 |

The single row is almost certainly the live-check session of the #234 deploy [inferred: its
date and engine v0.3 match that release].

A repeated-measures fit needs usable pairs, distinct athletes, depth per athlete, coverage
across templates and workloads, and athletes that can be held out independently. A raw row
count settles none of those, and this census fails all of them at zero. **What is missing is
athletes using the product, not collection plumbing.** No minimum-N threshold was invented:
none could be met, and the pass criterion is set when there is data to judge it on.

## What phase 8 left in place

| | State | Where |
|---|---|---|
| capture | improved | a047: `experience_level`, effective `workload_preference` plus `workload_preference_defaulted`, `prescription_branch` |
| report | landed | `app/scripts/dose_shadow_report.py`: census plus ratios, split by real vs seeded and by `v1_model_version` |
| fit policy | one module | `app/logic/dose_fit_policy.py` (`fit-policy-1`), shared by the census and the frame |
| real frame | implemented | `build_training_frame.load_shadow_frame`: one version, eligible rows, causal label, manifest plus fingerprint |
| C2b | fixed in v1.2 | the density axis compresses work (`work_volume_component`); v0 frozen and byte-identical |
| held-out leak | fixed | evaluation fits on held-in athletes only; features are standardized at fit time |
| activation | fail-closed | `app/logic/dose_calibration_artifact.py`: sha256-pinned artifact plus receipt, 16 named checks |
| fit | **NOT RUN** | the 2026-09-27 census had 0 usable pairs |
| validation | **NOT RUN** | nothing to hold out |
| v1 production | **OFF** | `PRODUCTION_DOSE_MODEL = "v0"`; v1 remains `EXPERIMENTAL_UNCALIBRATED` with no artifact |

### The label is causal

The label is the next session's RPE (1–4 whole days later) minus the athlete's mean RPE over
sessions strictly **before** this one, with at least 3 of them. A session without that
history has no label. It never borrows from the future.

Centring on the whole trajectory would give a held-out athlete's future RPEs to the
evaluation. Holding out whole athletes does not remove that optimism.

### Two leaks the old pipeline had, both removed

- `evaluate()` trained the prior on the whole frame before splitting, so the "held-out"
  athletes were in the fit.
- The features were z-scored over every row, test athletes included.

### Activation checks, in order

The first failure is named.

1. The artifact is declared, and its sha256 is pinned in the registry.
2. The file exists, and its bytes hash to the pin.
3. It is a valid `dose_overrides` artifact and not `shadow_only`.
4. It carries a receipt.
5. The receipt's `calibration_id` matches the registry.
6. Its `model_version` matches the engine's `DOSE_MODEL_VERSION` (v1.2).
7. Its `feature_schema_version` is `dose-features-2`.
8. Its `fit_policy_version` is `fit-policy-1`.
9. Its pairing rule is the current one.
10. Its `data_source` is `dose_model_shadow_log`; synthetic and seeded never pass.
11. Its validation split is `athlete_grouped`.
12. Its validation status is `pass`.
13. It carries a frame fingerprint, the code SHA and the counts.

The parameters come only from the verified bytes.

## What reopens 8B

1. Re-run the census after this release has been live for a while:

       sudo docker compose exec -T perf-lab-api python -m app.scripts.dose_shadow_report --json

2. Judge it on the `real` population of the live `v1_model_version` only (v1.2 from this
   release). Pre-release v1 and v1.1 rows are never pooled with it. Look at:
   - labelled pairs;
   - athletes with ≥ 2/3/5/10 eligible sessions;
   - athletes that can be held out (≥ 10 pairs);
   - coverage by modality, category, level, workload and prescription branch.

3. With enough data, `calibrate.build_calibration_artifact(load_shadow_frame(...))` produces
   the artifact and receipt. Activation is still a reviewed registry edit that pins the
   artifact's sha256; nothing activates by itself.

## Known limits, recorded rather than assumed away

- **C1: the other five axes still move with duration at fixed work.** The C2b fix makes the
  *density* axis depend on elapsed time only through Δ. Volume, intensity, impact, skill and
  metabolic still carry `log1p(V)`, whose volume proxy includes duration, and `Δ^β`. They are
  bit-identical to v1.1. Making them duration-invariant is backlog item C1, a separate
  dose-law question, not claimed here.
- **At a clamp, faster work ties rather than wins.** When both sessions sit at the same Δ
  bound, the clamp gives no further credit. The invariant is "faster ≥ slower", and strictly
  greater whenever Δ differs.
- **Recompute fidelity.**
  - The frame rebuilds each session from logged fields.
  - Novelty, exercise phi vectors and per-set external intensity are not persisted.
  - So the recomputed dose can differ from the dose recorded at ingest.
  - The manifest measures that gap (`recompute_fidelity`) instead of assuming it is zero.
- **Minimum N for a validation "pass" is open.** Evaluation's existing guards are minimum
  improvement, the sparse-athlete subgroup and the saturation fraction. None of them is a
  data-size floor. The receipt records the counts, and the floor is set when 8B has data.

## Census after the phase-8 deploy

Not deployed yet. Append the re-run census here after the release.
