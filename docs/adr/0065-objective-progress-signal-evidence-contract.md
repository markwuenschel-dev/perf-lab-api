---
status: proposed
date: 2026-07-11
---
# Objective progress is a derived signal over P10 evidence, not a column

P11 ([ADR-0050](0050-objectives-drive-training-emphasis.md)) makes objectives compute training
emphasis, and its gap term ([ADR-0061](0061-objective-target-share-function.md)) is
**confidence-aware**: a lagging objective pulls more only in proportion to how much we trust its
progress. That trust has to come from somewhere real. The temptation is to hang a mutable
`progress_pct` (and a `reliability`) directly on `Objective` and let the caller stamp it — the
same class of mistake [ADR-0058](0058-observation-provenance-capacity-authority.md) fixed for
capacity authority (a label smuggling in authority it never earned). Objective progress is a
**current, derived signal** over live observations, state confidence, freshness, and target
semantics — not a stored fact and not the seed tier of [ADR-0059](0059-seed-uncertainty-and-provisionality-views.md).

**Three separate models — intent, event, and derived signal never share a row:**

- **`Objective`** owns intent + lifecycle, with two **orthogonal** fields (not one fused enum):
  `lifecycle_status ∈ {active, completion_candidate, completed, cancelled}` and
  `objective_mode ∈ {pursuit, maintenance}`. `completion_candidate` is valid only for `pursuit`;
  `completed`/`cancelled` never enter the live mix; **`maintenance` is explicit** — reaching a
  target (`progress ≥ 100`) makes an objective a completion candidate, it never silently becomes
  perpetual maintenance (the "ghost maintenance" [ADR-0061](0061-objective-target-share-function.md)
  rejects). `domain` stays nullable.
- **`ObjectiveEvent`** carries taper, on its own record — never as columns on `Objective`
  (`event_type ∈ {competition, benchmark_attempt, milestone, review, deadline}`, `event_date`,
  `event_importance`, `taper_profile_code` nullable, `status`). **`taper_eligible` is derived**
  (`status active ∧ taper_profile_code ≠ null`), not an independently-writable flag that can
  contradict the profile. `event_type` alone grants no taper authority — only an explicit profile
  does, so a body-composition `deadline` cannot masquerade as a competition taper (see
  [ADR-0061](0061-objective-target-share-function.md)).
- **`ObjectiveProgressSignal`** is a **derived projection**, recomputed as-of a request, never a
  mutable authority column: `progress_pct` (nullable), `direction`, `evidence_status`
  (`measured|estimated|inferred|experience_derived|unknown`), `confidence_status`
  (`established|provisional|insufficient`), `source_observation_ids`, `source_state_version`,
  `computed_at`, `valid_as_of`, `progress_policy_version`, `reliability_policy_version`. It keeps
  the [ADR-0059](0059-seed-uncertainty-and-provisionality-views.md) distinction: `evidence_status`
  (provenance) ⟂ `confidence_status` (from live variance) — a real measurement can be *currently
  provisional* because it is old, noisy, or weakly related to the objective's target.

**The P10 → P11 contract is canonical evidence facts, not a pre-scored objective number.** P10
exposes an `EvidenceDescriptor` (`value_semantics`, `evidence_type`, `source_type`,
`collection_mode`, `capacity_effect`, `observation_ids`, `live_variance_by_axis`, `observed_at`,
authority/confidence policy versions). P11 owns `resolve_objective_progress(objective,
evidence_descriptor, athlete_state, as_of) → ObjectiveProgressSignal`. The split: **P10** answers
*what evidence exists, what it means, what authority it earned, how uncertain the state is*;
**P11** answers *given this objective's target and direction, what progress does that evidence
imply*. Lineage (source observations, state version, policy versions) is retained, not collapsed
into a vague label.

**`progress_reliability` is policy-derived, not a 1:1 label map.** `measured→1.0 / estimated→0.5
/ unknown→0.0` is too crude — a stale direct measurement with large live variance, or a
cross-axis inference, should not read as full authority. Reliability is computed from
`evidence_status` **plus** live variance on the relevant axes, freshness, directness of the
axis→objective mapping, and protocol validity, under a versioned `reliability_policy_version`.
[ADR-0061](0061-objective-target-share-function.md)'s gap then stays
`gap = reliability · observed_gap + (1 − reliability) · neutral_gap`, but `reliability` comes from
this resolver, not a caller-supplied string. **Missing P10 evidence fails to unknown / neutral,
never to `measured`.**

**ADR-0059's five seed tiers remain seed-*only*.** `direct_measured_onramp …
experience_prior … unseeded` answer *how an axis was initialized*, never *what objective-progress
quality is today*. They may inform the *first* `ObjectiveProgressSignal`, but reusing a seed tier
as the enduring runtime progress authority recreates exactly the stale-snapshot problem
[ADR-0059](0059-seed-uncertainty-and-provisionality-views.md) rejected for
`initial_seed_confidence` — after retests and workout evidence accumulate, progress quality is a
live fact.

**Domainless objectives are excluded from allocation, visibly.** A `domain = null` objective does
not enter modality-share allocation — and is **never** silently coerced to `general`/`mixed`
([ADR-0057](0057-domaincode-three-roles-one-vocabulary.md)). It records
`objective_mix_exclusion_reason = domain_missing` in the decision trace (so "absent from the mix"
never looks like an engine bug), yet still appears in countdowns/lifecycle and may drive **taper**
— which acts on the total-load budget `B_H`, not on modality shares, so it stays non-circular
([ADR-0061](0061-objective-target-share-function.md)).

**Sequencing: P10 ships the provenance/evidence implementation first; P11 does not build an
interim provenance model.** But P11 is not blocked wholesale — its independent slices (objective
lifecycle migration, `ObjectiveEvent` schema, taper selection, objective-mix pure functions, the
microcycle ledger, constraint interfaces, the receding-horizon solver, decision traces) proceed
against **fixtures implementing the frozen `EvidenceDescriptor` interface**; only P11's runtime
progress-evidence *adapter* gates on P10. Migration is conservative: legacy objectives backfill
`lifecycle_status = active` / `objective_mode = pursuit`; **no legacy `target_date` auto-becomes
taper-eligible**.

We rejected: **progress as a mutable `Objective` column** (a stored authority the caller stamps —
the ADR-0058 hole); **1:1 `reliability`-from-label** (ignores variance/freshness/directness);
**reusing ADR-0059 seed tiers as permanent progress authority** (stale-snapshot); **taper fields
on `Objective`** (contradictable flags); **coercing domainless objectives to `general`/`mixed`**
(fabricated emphasis); and an **interim P11-local provenance model** (throwaway that would fork
the vocabulary P10 owns).

Sits on [ADR-0058](0058-observation-provenance-capacity-authority.md) (evidence provenance +
authority) and [ADR-0059](0059-seed-uncertainty-and-provisionality-views.md) (seed-tier boundary +
the evidence/confidence split); consumes [ADR-0036](0036-per-axis-confidence-scalar.md) live
variance; feeds [ADR-0061](0061-objective-target-share-function.md) (the reliability consumer)
under the [ADR-0050](0050-objectives-drive-training-emphasis.md) umbrella.

**Guardrail:** objective progress is a *derived signal*, never a stored column. P10 owns evidence
facts; P11 owns the objective-specific transformation. Reliability is policy-derived from evidence
status **and** live variance/freshness/mapping — never a label lookup, and missing evidence fails
to unknown, never to measured. Seed tiers stay seed-only. Intent, taper event, and progress signal
are three separate records — none may masquerade as another.
