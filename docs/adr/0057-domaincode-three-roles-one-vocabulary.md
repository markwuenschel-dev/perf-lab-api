---
status: proposed
date: 2026-07-10
---
# DomainCode is one vocabulary with three distinct roles

[ADR-0038](0038-canonical-domain-taxonomy.md) made `domain_vocab.DOMAINS` the single
canonical domain/goal/modality taxonomy, with the guardrail *"new goals/sports are added as
aliases there, never as a new parallel vocabulary."* That guardrail is being violated: the
benchmark seed persists `mixed_modal` / `olympic_lifting` / `sprinting` (parallel spellings
not in `DOMAINS`, not aliased), and P10 is about to add three more domain-typed fields
(`benchmark_definition.domain_lenses`, `AthleteDomainLens.domain_code`, an operational
`Objective.domain`). Reconciling requires more than fixing spellings — it requires naming
what a "domain" *is* at each field, because three different meanings currently share the
word.

We decided a **`DomainCode` is one canonical vocabulary (`DOMAINS`) filling three distinct
semantic roles that must never be conflated as the same field**:

1. **Home domain** — a benchmark's / template's canonical specialist domain
   (`benchmark_definition.domain`, `coaching_template.domain`, operational `Objective.domain`).
2. **Surfacing lens** — the athlete-domain lenses under which a benchmark is *eligible to
   surface* in the onramp (`benchmark_definition.domain_lenses`). This is **discoverability
   metadata only** — never adaptation routing, prescription capability, or benchmark
   authority. Null resolves to `[domain]` as a *compatibility fallback*, and the system
   records `domain_lenses_source ∈ {explicit_curated, home_domain_default}` so a defaulted
   lens is distinguishable from a deliberately narrow one. A benchmark surfacing under
   `strength` does **not** make its home domain `strength`.
3. **Prescription capability** — `PRESCRIPTION_SUPPORTED_DOMAINS`, an **explicit reviewed
   `frozenset`** (`⊆ DOMAINS`; `== DOMAINS` for v1, including `general`). It is a capability
   *declaration*, not `= DOMAINS` aliased: a future domain added to the vocabulary must not
   become prescription-eligible until seed/exercise/constraint/onramp/dose/test support is
   deliberately added.

**Reach.** Canonicalization applies to every persisted field semantically typed `DomainCode`
— `benchmark_definition.domain`, every element of `domain_lenses[]`, `coaching_template.domain`,
`AthleteDomainLens.domain_code`, and `Objective.domain` (which is operational, so it
*validates* as canonical rather than being renamed). The **movement / exercise / weak-point /
category / tag vocabularies are a separate axis** (`candidate_library` branch keys,
`weak_point`/exercise tags, MPC keys) and are **not** touched by this effort, even where they
contain the strings `sprinting`/`olympic_lifting`.

**Mechanism.** Owned seed data must contain only `DOMAINS` values (CI fails on an alias in a
seed row); aliases (`DOMAIN_ALIASES`) normalize only *inbound* external/legacy input at a
boundary and are never persisted. The three legacy values are corrected in the seed
(`mixed_modal→mixed`, `olympic_lifting→weightlifting`, `sprinting→running`) — **no new global
aliases** — and an idempotent, set-based data migration normalizes any already-materialized
rows (safe on a clean DB; collision-free because `benchmark_definition.code` is the sole
unique key and observations reference by `benchmark_definition_id`, so it is a pure metadata
correction). Terminology is pinned so nobody reintroduces the folded spellings to "recover
clarity": **`weightlifting`** = Olympic weightlifting and derivatives (snatch/clean/jerk);
**`running`** = the broad running domain including sprinting, with sprint/endurance
distinctions living in `category`/tags below the domain.

We rejected adding `mixed_modal`/`olympic_lifting` aliases to excuse the seed (bakes the drift
in permanently), and `PRESCRIPTION_SUPPORTED_DOMAINS = DOMAINS` as a live alias (silently makes
future vocabulary additions prescription-eligible with no implementation).

Extends [ADR-0038](0038-canonical-domain-taxonomy.md); enables the [ADR-0047](0047-one-benchmark-assessment-surface.md)
domain-filtered onramp and the `AthleteDomainLens` model. Per-benchmark `domain_lenses`
curation is a **blocking closure requirement of the assessment-surface API work**, not this ADR.

**Guardrail:** one canonical `DomainCode` vocabulary, three non-interchangeable roles.
Discoverability (surfacing lens) is not classification (home domain) is not execution authority
(prescription capability). Canonical values are the only values persisted or serialized;
aliases live only at inbound boundaries; supported-for-prescription is an explicit reviewed set,
never a vocabulary alias.
