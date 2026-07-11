---
status: accepted
date: 2026-07-10
delivery: delivered
---
# Observation capacity authority is policy-derived, not caller-asserted

> **Delivered (P10 Slice 2, 2026-07-11).** New `app/logic/observation_authority.py`:
> the five provenance dimensions as vocabularies, a `capacity_effect`
> meet-semilattice, per-dimension caps (source/mode/evidence/protocol),
> server-derived `protocol_validity` (keyed on the definition's
> `standardization_rules`, never a client flag), and `resolve_authority` =
> `narrow_only(meet(caps), requested)` with hard denials in the caps and over-
> requests clamped-and-flagged (elevation can never be requested). The ADR-0055
> booleans are now **derived** from the resolved effect. Migration `a028` adds the
> additive nullable columns (source_type, collection_mode, provenance_operation,
> migration_version, migrated_at, actor_type, requested/`capacity_effect`,
> protocol_code/version/validity, authority_policy_version/resolution_reason,
> confidence_source/model_version), backfills every legacy row conservatively
> (workout→system/workout/none; else legacy_unknown/none — never fabricated into a
> measurement), and adds NOT VALID invariant guards. `create_observation` resolves
> authority and routes state by the four handlers.
>
> **Scoped promotion:** only `bidirectional_update` (measured, may regress) and
> `initialize_prior` (seed an empty twin, idempotency-guarded) mutate canonical
> state in this slice. `upward_lower_bound` is fully resolved + recorded, but
> promoting its floor-ratchet to live capacity is **deferred** — flipping it now
> would change the deployed ADR-0055 invariant (a workout-derived estimate never
> mutates canonical capacity) on the highest-risk path. Resolved authority and the
> applied transition are recorded **separately**: migration `a029` +
> `capacity_floor_shadow_log` + `capacity_floor_shadow_service` capture each deferred
> candidate — the proposed floor, projected uplift, `application_policy_version`
> (`capacity_floor_apply_v0_shadow_only`, distinct from the authority policy
> version), and the not-applied reason (`upward_lower_bound_promotion_deferred` /
> `below_watermark_no_uplift`) — as `decision_impact = none_shadow_only`. Live
> floor-ratchet activation is a **separate, observable promotion decision** requiring
> this shadow evidence, an idempotency proof, bounded-uplift guards, canary rollout,
> and rollback. `floor_capacity_at_prior` + `capacity_increased` helpers exist for
> when it graduates. Verified: 33 new/updated unit + DB tests green (resolver
> min-of-caps/hard-denials/narrow-only, the capacity-corruption invariants, and the
> floor-shadow capture), a028/a029 up/down/up + backfill on local Postgres,
> ruff+pyright clean, OpenAPI unchanged + web types regenerated, web build green.

[ADR-0055](0055-strength-evidence-ledger.md) established that only protocol-grade benchmark
observations may update capacity bidirectionally; workout extraction may raise a lower bound
but never regress. But the write path still carries authority on a free-form `source` string
and a `_resolve_authority` whose non-workout `else` branch defaults everything to
capacity-authoritative, `measured`, *bidirectionally regressing* — so P10's new onramp writes
(an *estimated* seed guess) would corrupt the twin as a hard measurement, the exact PR1 class
of bug. And P10 needs to distinguish *seeding* the twin from *updating* it, which two booleans
(`affects_capacity`, `can_regress_capacity`) cannot express.

We separate **five orthogonal provenance dimensions** — conflating them is what lets a label
smuggle in authority it never earned:

- **`source_type`** — origin/actor: `athlete_entry`, `workout_extraction`, `legacy_unknown`
  (built); `coach_entry`, `device_import`, `third_party_import` (in the taxonomy, **rejected at
  the write boundary** until a real writer/validation/UX/test surface ships).
- **`collection_mode`** — workflow context: `onboarding_onramp`, `retest`, `ad_hoc`, `workout`,
  `legacy_unknown`. Orthogonal to source (the same protocol can be athlete- or coach-entered in
  onramp or retest). Migration is **not** a mode — it is a separate `provenance_operation`
  (`schema_backfill` + `migration_version` + `migrated_at`), never overwriting origin.
- **`evidence_type` + `value_semantics`** (`measured|estimated|lower_bound|unknown`) — what the
  value means.
- **Protocol identity + validity** — a minimal typed, versioned `BenchmarkAuthoritySpec`
  (application registry keyed by `benchmark_code`: `required_inputs`, `allowed_value_semantics`,
  `maximum_effect_by_mode`). `protocol_validity ∈ {not_evaluated, incomplete, valid, invalid}`;
  only **server-derived `valid`** unlocks protocol authority. Free-text `measurement_protocol`,
  field presence, or a client-asserted validity flag authorize nothing. A standalone
  DB-backed protocol registry is deferred until protocol volume justifies it.
- **`capacity_effect`** — the state-transition **operator** the observation may perform:
  `none | initialize_prior | upward_lower_bound | bidirectional_update`. Implemented as **four
  distinct handlers**, not one residual path with flags. `initialize_prior` seeds an uncertain
  prior only when no authoritative state exists (**idempotency guard**: it may not overwrite an
  already-established twin); `upward_lower_bound` raises a floor, never lowers; only
  `bidirectional_update` may regress. Legacy booleans, if kept, are *derived* from this.

**Authority is the minimum of independent caps, and callers may only narrow it:**
`resolved = narrow_only(min(source_cap, mode_cap, evidence_cap, protocol_cap), requested)`, with
**hard denials first** (`workout_extraction`/`onboarding_onramp` → never bidirectional;
unknown/`legacy_unknown` semantics → always `none`; `protocol_validity != valid` → never
bidirectional). A caller may request *less* authority; **authority elevation is policy-derived
and can never be requested** — internal over-requests are rejected (clamp only where
compatibility demands, with a metric). This is the permanent structural fix for the PR1
corruption: a wrong writer can no longer assign authority it doesn't deserve. Every row records
`authority_policy_version` + `authority_resolution_reason`.

**P10 schema (additive, nullable):** `source_type, collection_mode, evidence_type,
value_semantics, requested_capacity_effect, capacity_effect, protocol_code, protocol_version,
protocol_validity, authority_policy_version, authority_resolution_reason, actor_type` (+ reuse
existing `created_by`/`user_id` for actor identity; `athlete_entry ⇒ actor=athlete, id
required`; `workout_extraction ⇒ actor=system, source set/log ids required`). A **confidence
hook** (`confidence_value` nullable — unknown stays NULL, `confidence_source`,
`confidence_model_version`) is structural here; **#106** assigns the numbers, calibration, and
measurement-debt consequences. **Conservative backfill only** — no migration elevates authority
from an old label; ambiguous legacy `manual` rows become `legacy_unknown` / `none`, not
`athlete_entry` / `measured`.

We rejected `source_type`-determines-authority (a label is not validity), caller-values-win
(the PR1 hole), and dead import/device columns (false import-readiness with no adapter enforcing
units/dedup/provenance).

Extends [ADR-0055](0055-strength-evidence-ledger.md); the observation write path for the
[ADR-0047](0047-one-benchmark-assessment-surface.md) assessment surface. Deferred: DB-backed
protocol registry, import allowlist + import-specific schema, coach/device writer paths, and
(under a #109 T0 attestation) the shadow old-vs-new authority comparison — replaced by a
migration dry-run + post-migration invariant query + authority-distribution report.

**Guardrail:** provenance is wide; capacity authority is narrow and policy-derived. Five
dimensions stay separate; authority is `min` of caps; callers narrow only; only server-validated
protocol grade unlocks bidirectional regression; ambiguous history is `legacy_unknown`, never
fabricated into measurement.
