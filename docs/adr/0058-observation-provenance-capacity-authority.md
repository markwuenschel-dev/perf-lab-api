---
status: proposed
date: 2026-07-10
---
# Observation capacity authority is policy-derived, not caller-asserted

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
