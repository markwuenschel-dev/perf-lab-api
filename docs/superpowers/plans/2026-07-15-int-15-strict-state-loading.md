# INT-15 W1-A — strict-by-default engine_state loading

Status: **decision locked 2026-07-15**, slice 1 committed (`6948947`), slices 2A–2D open.
Branch: `feat/int-15a-engine-state-codec`. No PR until the codec is wired and does something.

## Locked decision

> W1-A uses strict-by-default state loading.
>
> `load_current_state` and `unified_from_athlete_row` accept only complete, supported
> canonical engine-state payloads. They do not reconstruct from legacy columns.
>
> A separately named `load_current_state_for_display` function may return
> provenance-marked degraded reconstruction for explicitly approved, non-prescriptive,
> non-mutating display surfaces.
>
> Decision, readiness, benchmark, onboarding-existing-state, assessment-authority, and
> canonical mutation paths remain strict. Shadow services use a separate skip-oriented
> adapter, and repair tooling reads the raw payload through a forensic interface.
>
> Permissive recovery is therefore an explicit capability selected only by approved
> display callers. It is never the shared default and never returns an unmarked
> canonical state.

### Why not "add a strict adapter for the two prescription call sites" (rejected)

That leaves silent legacy reconstruction embedded in the shared seam
(`unified_from_athlete_row`), which fails three ways:

1. **Provenance dies at the loader boundary.** Recovery returned as an ordinary
   `UnifiedStateVector` is indistinguishable from canonical state. Any downstream helper
   may reuse it for prescription, mutation, or benchmark processing. The two prescription
   call sites are strict *today*; the next decision caller inherits permissiveness.
2. **The "read-only" caller set was misclassified.** See the table below — it was assigned
   by module name, not by capability. Four of the callers filed as read-only can gate
   prescription or mutate canonical state.
3. **It recreates permissive-by-default.** Better validation immediately followed by
   silent fallback is the old architecture. Exceptional authority must be explicitly
   requested, not inherited from a generic loader (fail-safe defaults; Saltzer &
   Schroeder, 1975).

## Call-site classification

Burden of proof: **strict unless the caller is demonstrably display-only.**

| Caller | Policy | Reason |
|---|---|---|
| `prescription_service.py:226,275` | Strict | Sizes and issues training |
| `readiness_service.py:378` | Strict | Readiness is a prescription/safety input |
| `benchmark_service.py:349,352` | Strict | Evidence + canonical-state mutation boundary |
| `onboarding_service.py:39` | Strict, or explicit initialization | Existing corrupt state must not become a fresh default |
| `assessment_surface_service.py:143` | Strict by default | May drive recommendation or eligibility |
| `dashboard_service.py:291` | Read-only recovery allowed | Display surface |
| `history.py:41` | Read-only recovery allowed | Historical display |
| `ekf_shadow_service.py` (×3) | Skip | Shadow absence must not block production |
| `recovery_shadow_service.py:39` | Skip | Same |
| repair utility | Raw forensic access | Must inspect the exact persisted payload |

## The bridge stops choosing policy

`unified_from_athlete_row` becomes a strict structural conversion: valid supported
`engine_state` → `UnifiedStateVector`; missing/malformed/incomplete/future → typed failure.

It must NOT: inspect caller purpose, use legacy fallback, log and continue, create
defaults, or decide the operation is read-only.

The old reconstruction branch (`state_bridge.py:212-225`) moves to a dedicated
compatibility projector used ONLY by the display loader:

```python
def reconstruct_legacy_state_for_display(row: AthleteState) -> ReadOnlyStateView: ...
```

## Return types differ — provenance cannot be dropped

```python
@dataclass(frozen=True)
class ReadOnlyStateView:
    state: UnifiedStateVector
    source: Literal["canonical", "legacy_recovery"]
    degraded: bool
    degradation_reason: str | None
    codec_version: str

async def load_current_state(...) -> UnifiedStateVector: ...          # strict
async def load_current_state_for_display(...) -> ReadOnlyStateView: ...  # explicit
```

`ReadOnlyStateView` must not expose prescription or mutation operations.

## Execution — four verified commits on one branch

**2A — make the shared loader strict.** `load_current_state` + `unified_from_athlete_row`
raise typed errors on malformed/incomplete/future. No permissive fallback remains in
either. Tests: empty vectors → fail; partial vectors → fail; malformed current → fail;
future version → *distinct* failure; valid complete v2 → unchanged.
Some display tests may fail at this commit. Do not merge it alone; prove the strict
foundation independently.

**2B — wire strict decision and mutation callers.** Prescription, readiness, benchmark,
existing-state onboarding, assessment authority. Invalid canonical state ⇒ no
prescription, no commitment, no canonical mutation, no benchmark application, and an
explicit domain-level unavailable result. Map typed failures at every public boundary —
an expected invalid-state condition must never surface as an accidental 500.

**2C — add the read-only compatibility loader.** Wire ONLY `dashboard_service` and
`history.py`. Add another surface only after proving it cannot affect prescription,
readiness, eligibility, benchmark selection, state mutation, or objective progress.
Tests: malformed + complete legacy → degraded view, `source=legacy_recovery`, no
writeback; malformed + incomplete legacy → unavailable; future version → no recovery,
unavailable; empty vectors → display may degrade while prescription still refuses.

**2D — connected flow verification.** One malformed athlete row: dashboard/history →
degraded response; prescription path → `canonical_state_invalid`; benchmark observation →
no state mutation; DB `engine_state` → unchanged. This is the load-bearing proof that
permissive state cannot cross into decision authority.

## Slice 3 stays separate — shadows are not display

Shadow semantics differ: return no counterfactual, record a skipped reason, preserve
production operation. No legacy reconstruction.

```python
async def try_load_current_state_for_shadow(...) -> ShadowStateLoadResult: ...
# loaded | skipped_malformed | skipped_incomplete | skipped_future_version | skipped_missing
```

## Onboarding needs an intentional operation

Two distinct operations, not one: new athlete with no state → explicit initialization
factory; existing athlete with malformed state → strict failure. Initialization must
never be `except MissingEngineState: default()`. Use
`initialize_athlete_state_from_prior(...)` carrying evidence source, variance policy,
initialization policy version, and unobserved/provisional semantics — so "missing state"
and "damaged state" cannot collapse into the same default constructor.

## Assessment surface — classify by output capability

The most ambiguous caller. Pure display of assessment history → recovery may be allowed.
Recommended benchmark / assessment progress / prescription eligibility / objective-progress
update → strict. **If one function does both, split it.** Do not assign a permissive policy
to a mixed-capability service because part of its output is visible in the UI.

## Failure mapping

Internal (preserve precision for observability + repair): `canonical_state_missing`,
`canonical_state_incomplete`, `canonical_state_malformed`,
`canonical_state_version_unsupported`.

External (may collapse):

```json
{ "code": "canonical_state_invalid",
  "prescription_available": false,
  "resolution_available_in_product": false }
```

Future-version stays distinguishable internally: it signals deployment incompatibility,
not damaged data. Operational response is "deploy readers," not "repair the row."

## Required architectural tests

- **No permissive fallback beneath the strict loader** — spy the compatibility projector;
  `load_current_state` on a malformed row must never invoke it.
- **No decision consumer imports the display loader** — structural import allowlist:
  `load_current_state_for_display` importable only by `dashboard_service` and the history
  route/service. Stronger than review memory. (Precedent: `tests/test_no_naive_utcnow.py`.)
- **Empty vector cannot size load** — `{"x":{},"f":{},"t":{}}` + populated legacy scalars ⇒
  prescription refuses, `_enrich_exercises_with_load` not called.
- **Display recovery cannot mutate** — no `db.add`, no flush, no commit, `engine_state`
  unchanged.
- **Strict callers stay strict after refactors** — parameterize the decision-call inventory
  (prescription, readiness, benchmark, onboarding-existing-state, assessment-decision);
  each gets the same malformed fixture and must fail closed.

## Observability — track policy outcome, not just codec failure

```
engine_state_load_total{purpose=decision|display|shadow|repair,
                        outcome=canonical|recovered|rejected|skipped}
engine_state_invalid_total{reason=empty_vectors|partial_vectors|malformed|future_version}
decision_operations_blocked_total{service, reason}
display_legacy_recovery_total{service, reason}
```

Invariants: decision loads recovered from legacy = 0; prescriptions from degraded state = 0;
canonical mutations from degraded state = 0; future-version recoveries = 0.

## Rollout safety — PRECONDITION, not a post-check

Run the read-only production sweep BEFORE merging 2B: empty x/f/t objects, partial vectors,
missing version, unsupported version, null `engine_state`, non-finite values. Then estimate
affected athlete rows, recent prescription activity among them, and recent benchmark/state
mutation activity.

This distinguishes "zero affected active athletes" from "strict deployment will block N
active athletes." Deployment behaviour is correct either way, but the second requires repair
readiness to land first.

**Known population at risk:** genuinely old rows with NULL `engine_state` bootstrap from
legacy scalars today and are legitimate. Under strict they refuse at every decision surface.
Fresh rows are safe — `athlete_state_kwargs_from_unified` writes `engine_state`
(`state_bridge.py:253`). Requires real DB access; not runnable from an agent session.

## Interaction with INT-05

INT-15 owns `state_bridge.py:204` and the `:206-225` branch. INT-05 owns `:227`
(`sync_legacy_from_vectors`). Adjacent diff hunks, separable concerns — INT-15 does not
block on INT-05. Note INT-05 is far larger than its original framing: the legacy scalar
*fields on `UnifiedStateVector`* feed `prescriber.py` and the constraint engine, not just
the web screens. See the INT-05 memory for the corrected blast radius.
