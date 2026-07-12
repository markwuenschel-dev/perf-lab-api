# Execution Contract: INT-02 — Single low benchmark must not durably regress max_strength

**Status:** DELIVERED 2026-07-12 _(all 11 tasks shipped on branch `feat/int-02-strength-decline-hysteresis`; forks A1/B1/C1 with the §8 amendments. Verified vs Postgres: 944 tests pass, ruff + pyright strict clean repo-wide, OpenAPI unchanged, migrations a032/a033 reversible. ADR-0066 written. State-correctness + prescription-shadow closure states complete; prescription-promotion gated OFF pending validation. PR open, not merged.)_

**Source:** candidate `INT-02` from the 2026-07-12 Repo Audit Swarm ledger (Algorithm/Numerical; priority +8; was `needs-human-decision`). Locked decision supplied by the user: *asymmetric downward confirmation hysteresis + temporary prescription protection + confirmed state regression via a bounded variance-aware estimator update — not an EWMA watermark, not a floor on current capacity.*

---

## 1. Executive mission

A single protocol-valid low benchmark currently regresses `capacity_x.max_strength` immediately and durably, and simultaneously drops the prescription load basis. Replace that with a downward-confirmation state machine: preserve the low observation, do **not** regress canonical capacity on first evidence, conservatively protect the next prescription, and apply a durable decline **only** after independent corroboration through a bounded Kalman-style update — never by overwriting state with the latest measured value.

## 2. Current baseline

- **Branch/state:** `main` @ `0b9c70f`, working tree clean. Migration head `a031_profile_date_of_birth` (single linear head). Latest ADR `0065`.
- **Runs today (verified by the audit swarm this session):** `ruff check .` clean, `pyright` strict `0/0/0`, `905` tests collect with 0 errors, CI runs the suite against real Postgres 16 and fails loudly, OpenAPI drift gate green.
- **Pre-existing unrelated failures:** none observed. DB tests require a reachable Postgres (`tests/conftest.py:92-94` skips when unavailable — run them against a live DB or they silently pass).
- **The corruption path (confirmed by direct read):** `app/services/benchmark_service.py:339` calls `apply_benchmark_observation(...)`; for `effect == CE_BIDIRECTIONAL_UPDATE` the signed residual in `app/logic/state_update_v0.py:147-204` (`residual01 = score01 - expected01`, unbounded negative, no floor) pulls the axis down, and `benchmark_service.py:356-358` persists the regressed `AthleteState` row. `floor_capacity_at_prior` (`state_update_v0.py:51`) protects only the `upward_lower_bound`/`initialize_prior` branches. **No hysteresis, confirmation, or retest-interval gate exists on the bidirectional path.**
- **Second durable surface:** prescribed load derives from the e1RM ledger, not the capacity axis — `app/services/prescription_service.py:144` `_current_e1rm_values` takes the latest `validity_status=='valid'` `BenchmarkObservation.raw_value` per code, consumed at `:189-206` `_enrich_exercises_with_load` → `app/logic/strength_calibration.py:207` `suggested_load_kg`. A single low valid observation drops this basis immediately.

## 3. Strategic meaning

This is the live re-emergence of the documented P9 state-corruption class on the highest-consequence axis. It sits directly on the ADR-0058 authority lattice: authority (`resolved_capacity_effect = bidirectional_update`, "this evidence is *eligible* to support a decrease") must be separated from application (`applied_capacity_effect`, "the transition policy exercises it") — the same resolved-vs-applied distinction ADR-0058 already locked for the shadow `upward_lower_bound` floor-ratchet. The fix generalizes that separation to the downward direction.

## 4. Scope

- A pure downward-decision policy: protocol/uncertainty-derived threshold, materiality test, transition classifier, bounded posterior update, temporary-ceiling formula — `strength_decline_policy_v1`.
- A durable `strength_decline_candidates` ledger (state machine: `stable → decline_candidate → confirmed_decline | dismissed | expired`).
- Interception of the `CE_BIDIRECTIONAL_UPDATE` **downward** branch in `create_observation` so a first material low observation creates a candidate instead of regressing canonical capacity.
- Independent-confirmation logic (two qualifying observations, separated by the definition's `minimum_retest_interval_days`, not the same source observation) → bounded estimator update on confirmation; dismissal on a stronger observation; expiry on deadline.
- A **candidate-aware prescription basis path** (not merely a final `min()`), gated by `ENABLE_DECLINE_CANDIDATE_PRESCRIPTION_BASIS` with three states — off (legacy latest-raw), shadow (compute + record both bases), on (canonical basis constrained by an active candidate ceiling; latest-raw is no longer direct durable authority). See §8 fork C.
- Semantics split: document `capacity_x.max_strength` as *current latent estimate*; expose a named `best_currently_validated_e1rm` accessor over the existing derived valid-e1RM watermark (best *currently valid* demonstrated strength — may fall on correction/quarantine, not an immortal maximum).
- `applied_capacity_effect` + `decline_transition_status` audit columns on `benchmark_observations`.
- Severe-acute-decline routing to the existing safety/readiness path rather than silent detraining. The decline policy only *identifies* a severe drop and routes the signal; the existing safety subsystem owns the response. No new clinical/contraindication logic in ADR-0066.
- Observability counters + the critical invariant metric `durable_strength_regressions_from_one_observation = 0`.
- **Three distinct closure states**, never collapsed into one green ticket: (1) *state-correctness complete* (first-obs regression blocked live + confirmed bounded update live), (2) *prescription-shadow complete* (both bases computed and compared), (3) *prescription-promotion complete* (flag on; latest-raw no longer durable prescription authority). While the flag is off, the prescription half of the defect is **not** closed.

## 5. Non-goals

- **Not** a global percentage-threshold retune (e.g. "ignore decreases < 5%"). Thresholds are protocol/uncertainty-derived; any global recalibration requires replay + shadow calibration and is a follow-up.
- **Not** an EWMA watermark as the decision mechanism, and **not** a monotone floor on *current* capacity (either would violate the locked decision).
- **Not** a new persisted `best_validated_strength` column in this mission (fork B recommends reusing the derived ledger watermark).
- **Not** broad cleanup of the 980-line `state_service` (INT-27) or the legacy-scalar mirror (INT-05), even where tasks read adjacent code.
- **Not** promotion of the ADR-0058 upward floor-ratchet — that remains shadow-only.
- **Not** a compatibility shim over the old immediate-regression behavior; the old behavior is a bug and is removed.

## 6. Blast-radius summary

| Surface | Impact | Source |
|---|---|---|
| State-update contract (bidirectional downward) | behavior change: regression deferred | `state_update_v0.py:147-204`, `benchmark_service.py:339-358` |
| `benchmark_observations` schema | +2 additive nullable cols (`applied_capacity_effect`, `decline_transition_status`) | model `:82-93`, migration NEW `a033` |
| New table `strength_decline_candidates` | new model + migration `a032` | template `capacity_floor_shadow.py` |
| Prescription read-path | candidate-aware basis path (off/shadow/on) replaces latest-raw authority | `prescription_service.py:144,189-206`, `strength_calibration.py:207` |
| Feature flags | +1 `ENABLE_DECLINE_CANDIDATE_PRESCRIPTION_BASIS` | `app/engine/feature_flags.py:6-15` |
| OpenAPI + web types | regen **iff** the candidate/summary is surfaced via `shadow_summary_service`/`shadow.py` (optional read side) | `openapi.json`, `web/src/types.gen.ts` |
| Existing invariants | `test_capacity_corruption_hotfix.py` must stay green (bidirectional *upward* + workout-extraction gates unchanged) | `tests/test_capacity_corruption_hotfix.py` |

## 7. Contracts / seams involved

- **Authority lattice (owner: `app/logic/observation_authority.py`, ADR-0058):** `resolve_authority` yields `capacity_effect` = resolved authority. This mission adds the *applied* effect as a distinct recorded value; it never elevates resolved authority. `capacity_effect_of(obs)` remains the fail-closed re-derivation.
- **State evolution (owner: `app/logic/state_update_v0.py`):** `apply_benchmark_observation` + `_apply_capacity_residual` remain the assimilation math; the new policy gates *whether the downward result is persisted*, mirroring how `floor_capacity_at_prior` post-processes the lower-bound branch.
- **e1RM ledger (owner: `BenchmarkObservation` rows):** the append-only observation history is already the monotone "best validated" record (max over non-quarantined valid `raw_value`, `state_service.py:759` `_e1rm_watermark`). This mission names it, not replaces it.
- **Retest interval (owner: `BenchmarkDefinition.minimum_retest_interval_days`, `:50`):** currently dormant; becomes the confirmation-separation rule.
- **Prescription load (owner: `prescription_service._enrich_exercises_with_load`):** the injection seam for the temporary ceiling.

## 8. Human decisions — resolved

All three forks are decided (user, 2026-07-12). Recorded here as binding; ADR-0066 restates them.

**Fork A — Threshold data source → A1, amended.** The threshold hierarchy selects only the *measurement-error component*; it does **not** replace the locked variance-aware threshold. The material-decline threshold is:

```
material_decline_threshold = max( measurement_error_threshold,
                                  z_down × sqrt(prior_variance + observation_variance) )
```

`measurement_error_threshold` is resolved by precedence:
1. definition-specific **MDC** present → use directly;
2. definition-specific **SEM** present, MDC absent → derive `MDC95 = 1.96 × sqrt(2) × SEM` (versioned formula);
3. **both** present → MDC governs; validate and warn if MDC and the SEM-derived value are materially inconsistent;
4. neither → validity-profile observation variance feeds only the `z_down` term, and the measurement-error component falls back to a conservative versioned constant under `strength_decline_policy_v1`, `calibration_basis = synthetic_and_expert_prior`.

The fallback must be explicitly provisional, observable, and replayable. **No calibration gate** blocks the first-observation intercept. **No global threshold retune** in this mission.

**Fork B — watermark → B1, naming corrected.** Reuse the derived `_e1rm_watermark`; **no new column**. Name and document it `best_currently_validated_e1rm` = *best currently valid demonstrated strength*, derived from `max(valid, non-quarantined raw_value)` — monotone when valid observations are added, **allowed to fall** when the top observation is corrected/invalidated/quarantined (a bad observation must not stay immortal to preserve monotonicity). Semantic split:
- `_e1rm_watermark` → best *currently valid* demonstrated performance;
- `capacity_x.max_strength` → current latent strength estimate;
- decline-candidate ceiling → temporary prescription constraint.

Data correction and physiological decline are **distinct transition reasons** and must not be conflated (see T2 regression test).

**Fork C — live vs shadow → C1 as a staged rollout, amended.** Two parts land at different maturities:
- **State correction ships live:** a first material low bidirectional benchmark → obs retained, decline candidate created, `applied_capacity_effect = none`, canonical `max_strength` unchanged. Confirmed regression also ships live (it strictly narrows existing behavior): two independent qualifying observations + minimum retest interval + valid protocol + material residual → bounded bidirectional update.
- **Prescription basis is staged behind `ENABLE_DECLINE_CANDIDATE_PRESCRIPTION_BASIS`** — the flag governs the *entire* candidate-aware basis path, not an incidental `min()`:
  - **off:** legacy `latest valid raw e1RM` remains the basis. Capacity-axis bug fixed; **prescription-basis bug not yet closed** — do not declare the numerical-correctness item fully resolved in this state.
  - **shadow:** compute both `legacy_basis = latest valid raw e1RM` and `candidate_aware_basis = min(canonical current-latent basis, active candidate ceiling)`; record `legacy_basis, candidate_aware_basis, absolute_delta, relative_delta, selected_basis (=legacy), candidate_id, ceiling_semantics, policy_version`.
  - **on:** `effective_basis = min(normal_basis, active decline-candidate ceiling)` where `normal_basis` = canonical current max-strength basis. Ceiling disappears on dismiss/expire; on confirm, canonical strength updates via the bounded estimator and becomes `normal_basis`.
  - **Critical invariant (flag on):** `prescription_service` must **not** select the latest raw benchmark merely because it is chronologically latest — that removal is the actual second-surface fix.

> Categories: **policy/SME** (A), **schema/migration** (B), **architecture/rollout** (C). Recorded as decisions, not re-litigated in tasks.

## 9. Implementation strategy

Decided shape (per the locked decision): an **asymmetric downward state machine** layered on the existing authority lattice, keeping observation, candidate, and confirmed-application as three separate recorded facts.

```
observe (always stored, never clamped up)
  └─ effect == bidirectional_update AND residual downward?
        ├─ |delta_down| < downward_threshold  → immaterial: no regression; obs recorded; may widen variance
        └─ |delta_down| ≥ downward_threshold  → decline_candidate
                 applied_capacity_effect = none
                 temporary_prescription_ceiling active (flagged)
                 ── await independent corroboration ──
                     ├─ 2nd qualifying downward obs (independent occurrence, ≥ min_retest_interval,
                     │   directionally consistent, distinct source_observation_id, beyond error band)
                     │       → confirmed_decline: bounded posterior = prior + K·(obs − prior)
                     ├─ stronger qualifying obs → dismissed; ceiling removed
                     └─ confirmation_deadline passed → expired
```

Upward `bidirectional_update`, `upward_lower_bound`, `initialize_prior`, and `none` paths are untouched. A severe unexplained acute drop routes to the safety/readiness path, not auto-detraining.

**Rejected alternatives** (named per the locked decision): EWMA watermark (obscures elapsed time / conflates protocols / a run of fatigued tests still drags it down / opaque smoothing coefficient); measured-path floor on current capacity (prevents genuine post-detraining/injury decline → unsafe prescriptions); overwrite `current_strength = latest_low_value` (the current bug).

## 10. Task graph

```
T1 ─┐ (pure policy)
T2 ─┤ (semantics + watermark accessor)          parallel
T3 ─┘ (candidate model + a032)
        └─ T4 (obs audit cols + a033; depends T3 for migration head)
              └─ T5 (ingestion intercept + service)   depends T1,T2,T3,T4
                    ├─ T6 (confirmation state machine + bounded update)   depends T5
                    ├─ T7 (candidate-aware prescription basis 7a-7d)      depends T5   ── parallel with T6,T8
                    └─ T8 (severe-acute-decline safety routing)           depends T5
                          └─ T9 (observability + invariant metric)        depends T5,T6
                                └─ T10 (P9 replay + full invariant matrix) depends all
                                      └─ T11 (capture: ADR-0066, docs, memory, ledger)
```

## 11. Task-by-task plan

### T1 — Pure downward-decision policy module
- **Depends:** none
- **Purpose:** encode the threshold hierarchy, materiality test, transition classifier, bounded posterior, and ceiling formula as a pure, versioned module.
- **Files:** `app/logic/strength_decline_policy.py` `NEW`
- **Action:** define `POLICY_VERSION = "strength_decline_policy_v1"`, `CALIBRATION_BASIS = "synthetic_and_expert_prior"`; `downward_threshold(*, protocol_error, prior_variance, observation_variance, z_down)` returning `max(protocol_mdc_or_none, z_down*sqrt(prior_var+obs_var))` with the A1 fallback and `MDC95 = 1.96*sqrt(2)*SEM` where only SEM exists; `is_material_decline(delta_down, threshold)`; `classify_transition(...)` → one of `stable|decline_candidate`; `bounded_posterior(prior, observation, gain)`; `temporary_ceiling(observed_value, buffer)`; provisional fallback constants clearly marked non-calibrated.
- **Check:** `tests/test_strength_decline_policy.py` `NEW` — pure cases incl. `prior 150 / retest 149` inside MDC → immaterial; `prior 150 / retest 138` beyond MDC → material; SEM→MDC95 derivation; fallback path flagged provisional; ceiling = `observed + buffer` never equals raw low value nor prior max.
- **Verify:** `uv run pytest tests/test_strength_decline_policy.py -q` → green; `uv run ruff check app/logic/strength_decline_policy.py && uv run pyright app/logic/strength_decline_policy.py` → clean.
- **Risk/rollback:** pure module, no imports into hot path yet; delete file to roll back.

### T2 — Semantics split + `best_currently_validated_e1rm` accessor
- **Depends:** none
- **Purpose:** name the three meanings so no field carries two; expose the *currently-valid* watermark distinctly (fork B, corrected).
- **Files:** `app/domain/vectors.py` (docstring on `CapacityState.max_strength` = current latent estimate), `app/services/state_service.py` (add public `best_currently_validated_e1rm(db, user_id, code)` delegating to `_e1rm_watermark` `:759`, documented as best *currently valid* demonstrated strength — monotone on valid adds, may fall on correction/quarantine).
- **Action:** document the split (`_e1rm_watermark` = best currently valid; `capacity_x.max_strength` = current latent; ceiling = temporary constraint); no behavior change to existing callers.
- **Check:** `tests/test_best_validated_strength.py` `NEW` — accessor returns max valid non-quarantined `raw_value`; an unconfirmed low observation does not lower it; **quarantine the current top e1RM → the derived watermark falls to the next valid observation, no ordinary decline-candidate transition is manufactured, and the correction provenance stays auditable** (data-correction ≠ physiological-decline transition reason).
- **Verify:** `uv run pytest tests/test_best_validated_strength.py -q -m requires_db` → green; `uv run pyright` clean.
- **Risk/rollback:** additive accessor; revert the two edits.

### T3 — `strength_decline_candidates` model + migration a032
- **Depends:** none (parallel with T1/T2)
- **Purpose:** durable candidate ledger driving confirmation + ceiling.
- **Files:** `app/models/strength_decline_candidate.py` `NEW`, `app/models/__init__.py` (register), `alembic/versions/a032_strength_decline_candidates.py` `NEW`
- **Action:** table `strength_decline_candidates` modeled on `capacity_floor_shadow.py`, retaining: `candidate_id` (id PK), `user_id` FK+idx (athlete), `capacity_axis`, `benchmark_definition_id` FK + `benchmark_code` (canonical movement/exercise identity), `trigger_observation_id` FK benchmark_observations (idx), `trigger_assessment_occurrence_id`, `prior_mean`, `prior_variance`, `observed_value`, `observation_variance`, `measurement_error_threshold`, `normalized_residual`, `threshold_source`, `status` (`active|confirmed|dismissed|expired|safety_routed`), `confirmation_observation_id` FK nullable, `created_at`, `resolved_at`, `authority_policy_version`, `decline_policy_version`, `applied_posterior_mean` nullable. **Idempotency:** a unique constraint equivalent to `unique(trigger_observation_id, capacity_axis, decline_policy_version)` so replay cannot create parallel candidates. `a032` `down_revision="a031_profile_date_of_birth"`; up = create_table + indexes + unique constraint; symmetric downgrade.
- **Check:** `tests/test_strength_decline_candidate_model.py` `NEW` — row round-trips; the unique constraint rejects a duplicate `(trigger_observation_id, axis, policy_version)`; `compare_metadata` empty after upgrade.
- **Verify:** `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` clean; `uv run pytest tests/test_strength_decline_candidate_model.py -q -m requires_db` green.
- **Risk/rollback:** additive table; `alembic downgrade -1`.

### T4 — Observation audit columns + migration a033
- **Depends:** T3 (migration head ordering)
- **Purpose:** record resolved-vs-applied per ADR-0058 interaction.
- **Files:** `app/models/benchmark_observation.py` (add `applied_capacity_effect` String(30) nullable, `decline_transition_status` String(20) nullable), `alembic/versions/a033_observation_applied_effect.py` `NEW`
- **Action:** additive nullable columns; `a033` `down_revision="a032_strength_decline_candidates"`.
- **Check:** `tests/test_observation_applied_effect.py` `NEW` — a bidirectional low obs records `capacity_effect="bidirectional_update"` (resolved) and `applied_capacity_effect="none"` on first evidence.
- **Verify:** `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` clean; targeted test green.
- **Risk/rollback:** additive; downgrade one step.

### T5 — Ingestion intercept + decline service
- **Depends:** T1, T2, T3, T4
- **Purpose:** stop first-observation durable regression; create a candidate instead.
- **Files:** `app/services/strength_decline_service.py` `NEW`, `app/services/benchmark_service.py` (`create_observation`, the `apply_state`/bidirectional branch `:331-358`)
- **Action:** when `effect == CE_BIDIRECTIONAL_UPDATE` and the per-axis residual is downward: compute `delta_down`/`combined_uncertainty`/threshold via T1; if immaterial → suppress the downward axis write (keep any upward axes, keep obs), set `applied_capacity_effect="none"`, `decline_transition_status="no_material_decline"`; if material → do **not** persist the regressed axis, create a `decline_candidate` row (best-effort `db.add`, no separate commit, mirroring `capacity_floor_shadow_service`), set `applied_capacity_effect="none"`, `decline_transition_status="decline_candidate"`. Upward/mixed and non-bidirectional paths unchanged. Service exposes `open_or_update_candidate(...)`.
- **Check:** extend the corruption suite: `prior 150 → valid retest 138` → one `strength_decline_candidates` row, canonical `max_strength` unchanged, obs stored, `applied_capacity_effect="none"`.
- **Verify:** `uv run pytest tests/test_strength_decline_flow.py -q -m requires_db` green; `uv run pytest tests/test_capacity_corruption_hotfix.py -q -m requires_db` still green.
- **Risk/rollback:** highest-risk task; guarded by the unchanged-hotfix regression run. Revert the `benchmark_service` diff + delete service to restore prior behavior.

### T6 — Confirmation state machine + bounded update
- **Depends:** T5
- **Purpose:** durable decline only on independent corroboration, via bounded estimator.
- **Files:** `app/services/strength_decline_service.py`, `app/services/benchmark_service.py` (call the confirmation check when a new qualifying obs lands)
- **Action:** on a new downward qualifying obs, find an open candidate for the axis; **confirm iff all hold**: distinct `trigger`/`confirmation` observation ids, **different assessment occurrences**, `observed_at` separated by `≥ minimum_retest_interval_days` (definition without an interval → a **versioned conservative fallback**; null must not permit same-day confirmation), protocol valid for *both*, same canonical capacity target, directionally consistent → `confirmed`, apply `bounded_posterior` (T1) to canonical capacity (a real, bounded, auditable write), stamp `applied_capacity_effect="bidirectional_update"`, `decline_transition_status="confirmed_decline"`. A stronger qualifying obs → `dismissed` (+ ceiling removed). Past deadline → `expired`. **A single observation can never both create and later confirm the same candidate** (T3's unique constraint + the distinct-id/occurrence check).
- **Check:** `second independent retest 140` → `confirmed_decline`, bounded posterior reduction (not overwrite to 140-equivalent); `retest 151` → `dismissed`; `same obs replayed` → no confirmation, no duplicate effect.
- **Verify:** `uv run pytest tests/test_strength_decline_flow.py -q -m requires_db` green (confirmation/dismissal/replay cases).
- **Risk/rollback:** revert confirmation block; candidates simply expire.

### T7 — Candidate-aware prescription basis (staged, flagged)
- **Depends:** T5 — parallel with T6/T8
- **Purpose:** close the second corruption surface — the latest-raw prescription basis — via a staged, flag-governed basis path (fork C amendment). The flag controls the **entire** basis selection, not an incidental `min()`.
- **Files:** `app/engine/feature_flags.py` (`ENABLE_DECLINE_CANDIDATE_PRESCRIPTION_BASIS`, tri-state: off/shadow/on), `app/logic/strength_calibration.py` (`suggested_load_kg` gains an optional `ceiling_kg`), `app/services/prescription_service.py` (`_current_e1rm_values` `:144` + `_enrich_exercises_with_load` `:189-206`), a basis-shadow record surface consistent with existing shadow logging.
- **Action (sub-tasks — one vertical slice each):**
  - **7a** — add the candidate-aware basis computation + shadow comparison: compute `legacy_basis = latest valid raw e1RM` and `candidate_aware_basis = min(canonical current-latent basis, active candidate ceiling = temporary_ceiling(observed, buffer))`; record `legacy_basis, candidate_aware_basis, absolute_delta, relative_delta, selected_basis, candidate_id, ceiling_semantics, policy_version`.
  - **7b** — **off:** preserve legacy behavior exactly (`selected_basis = legacy_basis`); prescription output byte-identical to baseline.
  - **7c** — **on:** `effective_basis = min(normal_basis = canonical current max-strength basis, active candidate ceiling)`; ceiling disappears on dismiss/expire; confirmed decline makes the bounded-updated canonical value the `normal_basis`.
  - **7d** — **on:** remove latest-raw direct durable authority — `prescription_service` must not select the latest raw benchmark merely because it is chronologically latest (the actual second-surface fix).
- **Check:** flag off → prescription byte-identical + shadow record written; flag shadow → both bases recorded, `selected_basis == legacy_basis`; flag on → active candidate caps load to the ceiling AND a chronologically-latest low raw obs is **not** selected as basis.
- **Verify:** `uv run pytest tests/test_decline_prescription_basis.py -q -m requires_db` green (off-identical / shadow-records-both / on-capped / on-latest-raw-not-authority).
- **Risk/rollback:** default flag state per fork C; set off to restore legacy basis. Off state leaves the prescription-basis defect open by design — tracked as a distinct closure state (§14).

### T8 — Severe-acute-decline safety routing
- **Depends:** T5
- **Purpose:** a large unexplained drop enters the *existing* safety/readiness path, not silent detraining.
- **Ownership boundary (fork C amendment):** the decline policy only **identifies** a severe unexplained drop and **routes** the signal + records the result (`status = safety_routed`). The **existing safety subsystem determines the permitted response** — this implementation introduces **no new clinical/contraindication logic** in ADR-0066. A severe drop may legitimately end as: canonical state unchanged + candidate active + prescription conservatively constrained + the existing `safety_review_required` signal emitted — these outcomes are compatible.
- **Files:** `app/services/strength_decline_service.py`, `app/services/benchmark_service.py` (route into the existing safety/readiness signal, do not reimplement it)
- **Action:** when `delta_down` exceeds a severe multiple of the error band, set candidate `status = safety_routed` and emit the existing safety/review signal (counter + structured log); do not auto-apply detraining; do not add new safety rules.
- **Check:** a large unexplained drop routes to the existing safety signal, records `safety_routed`, and does not durably regress on that single observation.
- **Verify:** `uv run pytest tests/test_strength_decline_flow.py -q -k severe -m requires_db` green.
- **Risk/rollback:** additive signal; remove the branch.

### T9 — Observability + critical invariant metric
- **Depends:** T5, T6
- **Purpose:** make the guarantee measurable.
- **Files:** `app/services/strength_decline_service.py` (counters), a metrics/telemetry surface consistent with existing shadow logging.
- **Action:** counters — candidates created/confirmed/dismissed/expired, temporary caps, time-to-confirmation, confirmed magnitude, post-confirmation rebound, `single_observation_regressions`; expose the invariant `durable_strength_regressions_from_one_observation` (target 0).
- **Check:** across the P9 replay scenarios the invariant metric reads 0.
- **Verify:** `uv run pytest tests/test_strength_decline_observability.py -q -m requires_db` green.
- **Risk/rollback:** telemetry-only; remove counters.

### T10 — P9 replay + full invariant matrix
- **Depends:** T1–T9
- **Purpose:** prove the required invariants and negative fixtures loudly.
- **Files:** `tests/test_strength_decline_flow.py`, `tests/test_strength_decline_invariants.py` `NEW`
- **Action:** implement the full test list from §16 incl. negatives: low workout-extraction cannot create a bidirectional decline candidate; low ad-hoc without valid protocol cannot durably regress; P9 historical regression cases replay with zero single-observation durable regressions; `best_validated_strength` stays historical while `current_strength_estimate` decreases on confirmed decline.
- **Verify:** `uv run pytest tests/ -q -m requires_db -k "decline or corruption or observation_authority"` green; then full `uv run pytest -q`.
- **Risk/rollback:** tests only.

### T11 — Capture
- **Depends:** T10
- **Purpose:** single source of truth for the decision + status.
- **Files:** `docs/adr/0066-strength-decline-hysteresis.md` `NEW`, this plan (mark delivered), the audit report ledger (INT-02 → shipped), memory update.
- **Action:** ADR records the locked decision, the resolved forks (A1/B1/C1), rejected alternatives, and the invariant contract.
- **Verify:** ADR exists and is linked from `docs/adr/README.md`; contract §19 commands all green.
- **Risk/rollback:** docs only.

## 12. Execution mode

**Connected-impact sweep.** The mission changes a numerical/state-update contract (bidirectional downward assimilation), adds a table + two schema migrations, alters the prescription read-path, and adds audit columns whose read side may reach OpenAPI/web types. Per the family rule, execution follows `connected-impact-sweep` with this contract supplying scope and gates; the sweep must confirm the ADR-0058 authority callers, `test_capacity_corruption_hotfix.py`, and any `shadow_summary_service`/OpenAPI/web-types surface stay coherent. (Ledger `execution_mode` was `blocked-needs-human-decision`; the §8 fork resolutions release it to connected-impact-sweep.)

## 13. Required commands

```bash
uv run pytest -q                                  # full suite (needs live Postgres for -m requires_db)
uv run pytest -q -m requires_db -k decline        # this mission's DB tests
uv run ruff check .
uv run pyright
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head   # a032/a033 reversibility
uv run python -m app.scripts.export_openapi --check           # only if a read side is surfaced
```

## 14. Verification gates

- **Per task:** each task's Verify command goes from red (or absent) to green before the next dependent task starts.
- **Phase gate after T5:** `test_capacity_corruption_hotfix.py` green (no regression of the upward/workout-extraction invariants) **and** the new "138 → candidate, mean unchanged" test green.
- **Phase gate after T6/T7:** confirmation/dismissal/replay green; flag-off prescription byte-identical; shadow records both bases.
- **Three closure states — reported separately, never collapsed into one green ticket:**
  1. **state-correctness complete** — first-obs regression blocked live + confirmed bounded update live (T5, T6 green; hotfix suite green).
  2. **prescription-shadow complete** — both bases computed and compared, shadow records written (7a green).
  3. **prescription-promotion complete** — flag on; latest-raw no longer durable prescription authority (7c/7d green). *Until this state, the prescription half of the defect is open — INT-02 is not fully resolved.*
- **Final:** full `uv run pytest -q` green, `ruff`/`pyright` clean, both migrations reversible, OpenAPI `--check` green (or unchanged), invariant metric `durable_strength_regressions_from_one_observation == 0` across P9 replay.

## 15. Failure codes

```
FAIL-SCOPE-CREEP        — work expanded beyond INT-02 (e.g. touched INT-05/INT-27).
FAIL-PHANTOM-TARGET     — a task named a file absent from baseline and not marked NEW.
FAIL-UNVERIFIED-TASK    — a task reported done without its Verify output.
FAIL-FAKE-GREEN         — a gate passed while requires_db tests silently skipped (no live DB).
FAIL-BURIED-DECISION    — a §8 fork (A/B/C) resolved inside a task instead of at the contract.
FAIL-REGRESSION-ON-ONE  — a single low observation durably regressed canonical capacity (core invariant breached).
FAIL-OVERWRITE-UPDATE   — a confirmed decline overwrote state with the latest value instead of a bounded posterior.
FAIL-EWMA-OR-FLOOR      — an EWMA watermark decided reality, or a monotone floor was imposed on current capacity.
FAIL-CONFIRM-REPLAY     — the same observation confirmed a decline (duplicate-evidence hole).
FAIL-LATEST-RAW-BASIS   — flag on, yet prescription still selected the chronologically-latest raw benchmark as basis.
FAIL-SAFETY-REIMPL      — new clinical/contraindication logic added in ADR-0066 instead of routing to the existing safety subsystem.
FAIL-CANDIDATE-DUP      — replay created a parallel candidate (idempotency constraint absent or bypassed).
```

## 16. Negative fixtures / adversarial checks

- Low `workout_extraction` observation → **cannot** create a bidirectional decline candidate (authority capped at `upward_lower_bound`).
- Low ad-hoc observation without valid protocol (`protocol_validity != valid`) → **cannot** durably regress capacity.
- Same observation id replayed → **no** confirmation, **no** duplicate applied effect.
- Decline inside the measurement-error band → **no** candidate, **no** regression.
- Feature-flag off → prescription output byte-identical to baseline; flag on → a chronologically-latest low raw observation is **not** selected as prescription basis.
- Confirmed decline → `best_currently_validated_e1rm` unchanged while `current_strength_estimate` (capacity axis) decreases via bounded update.
- Quarantine the current top e1RM → `best_currently_validated_e1rm` falls to the next valid observation, and **no** decline-candidate transition is manufactured (data-correction ≠ physiological-decline).

## 17. Review plan

- **Spec axis:** every §16 invariant has a passing test; resolved-vs-applied recorded on the observation; bounded posterior (not overwrite) on confirmation; ceiling `= observed + buffer`; thresholds protocol/uncertainty-derived with the fallback flagged provisional.
- **Quality axis:** the new policy module is pure and single-responsibility; the ingestion intercept mirrors the existing `floor_capacity_at_prior` post-process seam rather than forking a parallel path; best-effort candidate writes never break the observation commit; no widening of public interfaces beyond the two additive columns + one accessor; dependency direction (`services → logic`, not reverse) preserved.

## 18. Merge gate

Open the PR when: full `uv run pytest -q` green on live Postgres, `ruff`/`pyright` clean, `a032`+`a033` up/down/up clean, `test_capacity_corruption_hotfix.py` green, the invariant metric reads 0 across P9 replay, and (if a read side was added) OpenAPI `--check` green with web types regenerated. **Open PR and stop — do not merge** (house rule; merge is a separate human act).

## 19. Definition of done

Running the §13 commands answers done/not-done with no judgment. Reported against the **three closure states** (§14), not one flag:
1. `uv run pytest -q -m requires_db -k decline` → all green (materiality, candidate, confirmation, dismissal, replay, severe, basis off/shadow/on, negatives).
2. `uv run pytest tests/test_capacity_corruption_hotfix.py -q -m requires_db` → green (no invariant regressed) — **state-correctness complete**.
3. Basis shadow test records both bases → **prescription-shadow complete**.
4. Flag-on test proves latest-raw is no longer basis authority → **prescription-promotion complete** (the point at which the prescription half is closed).
5. `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` → clean.
6. `uv run ruff check . && uv run pyright` → clean.
7. Invariant assertion `durable_strength_regressions_from_one_observation == 0` in the observability test → green.

## 20. Follow-ups

- **B2:** persist a monotone `best_validated_strength` column/table if the derived-watermark accessor proves insufficient.
- **Threshold calibration:** shadow the residual/confirmation distributions, then retune `z_down`/buffer/MDC off real data (out of scope per §5).
- **Generalize** the downward-hysteresis gate to non-strength bidirectional capacity axes (aerobic, power) once strength is proven.
- **INT-05** (lossy legacy-scalar mirror feeding prescriber safety) and **INT-16** (numerical property tests / EKF PSD) are sibling ledger candidates — the property-test harness from INT-16 would strengthen this mission's invariants; both stay separate loops.
- **Surface** `strength_decline_candidates` in `shadow_summary_service`/`app/api/v1/shadow.py` for operator visibility if the read side isn't added in T-tasks.
```
