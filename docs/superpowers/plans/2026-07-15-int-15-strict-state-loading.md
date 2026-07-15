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

## Execution — expand-contract, one authority class per commit

**Superseded 2026-07-15:** the original sequence flipped the shared loader strict FIRST.
That creates a temporarily broken integration state — a commit where
`unified_from_athlete_row` refuses rows its unclassified consumers still expect to
receive. "Do not merge it alone" is not a substitute for not creating it. Strict and
display loaders are now introduced **additively** before any shared loader changes
behaviour, so every intermediate commit is independently verified and reviewable.

```
2A1  strict loader, additive          ← DONE (c4af4e0), ungated
2A2  display loader, additive         ← DONE (c4af4e0), ungated
2B1  prescription                     ← GATED: sweep
2B2  readiness                        ← GATED: sweep
2B3  benchmark                        ← GATED: sweep only (e1RM gate CLOSED, false alarm)
2B4  onboarding / assessment          ← GATED: sweep
2C   dashboard / history
S3   shadows (separate slice)
2D   retire the temporary generic compatibility loader
2E   connected-flow proof
```

**No commit touches more than one authority class.**

**2A1/2A2 — additive, unblocked.** Add `load_current_state_strict(...)` and
`load_current_state_for_display(...) -> ReadOnlyStateView` side by side. Leave the
existing `load_current_state(...)` **unchanged temporarily**.

> **Structural rule: no new caller may use the temporary compatibility loader.**
> Add an import allowlist test pinning its existing importers. **The allowlist may only
> shrink** — it is the contract that makes the temporary state temporary.

Strict-loader tests: empty vectors → fail; partial vectors → fail; malformed current →
fail; future version → *distinct* failure; valid complete v2 → unchanged result.

**2B — wire strict decision and mutation callers, in order.** Invalid canonical state ⇒ no
prescription, no commitment, no canonical mutation, no benchmark application, and an
explicit domain-level unavailable result. Map typed failures at every public boundary —
an expected invalid-state condition must never surface as an accidental 500.

- **2B1 prescription** (`:226,275`) first — the two direct load-sizing callers. Test:
  empty vectors + populated legacy scalars ⇒ strict failure, `_enrich_exercises_with_load`
  NOT called, no prescription revision, no commitment, explicit `canonical_state_invalid`.
- **2B2 readiness** (`:378`) — not display-only; influences prescription, must refuse.
- **2B3 benchmark** (`:349,352`) — e1RM transaction ownership is PROVEN (GATE 2 closed;
  `create_observation` commits at `benchmark_service.py:428`). Gated on the sweep only.
  Note when wiring: `create_observation` commits internally, so a strict refusal must happen
  BEFORE the call — once it returns, the observation cannot be rolled back.
  Invariant: malformed current state ⇒ canonical state is NOT mutated from degraded
  reconstruction. Whether the observation itself commits when state application fails is
  an explicit product transaction decision. Recommended default: structured benchmark
  submission + state application is atomic; invalid pre-existing canonical state rejects
  the command *before* writing new benchmark evidence. A future recovery workflow may
  ingest evidence without applying state — as a separately named mode, never an accidental
  partial success.
- **2B4 onboarding / assessment** — split by capability, not service name. New-athlete
  explicit initialization → initialization factory. Existing malformed state → strict
  refusal. Display-only assessment history → recovery allowed. Benchmark recommendation,
  eligibility, or progress authority → strict.

**2C — switch dashboard/history to explicit display recovery.** Wire ONLY
`dashboard_service` and `history.py`. Add another surface only after proving it cannot
affect prescription, readiness, eligibility, benchmark selection, state mutation, or
objective progress. Tests: malformed + complete legacy → degraded view,
`source=legacy_recovery`, no writeback; malformed + incomplete legacy → unavailable;
future version → no recovery, unavailable; empty vectors → display may degrade while
prescription still refuses.

**2D — contract.** Once every caller is classified, either rename the strict loader to
`load_current_state` or delete the old generic loader entirely. Prove no unclassified
caller remains (the allowlist is empty). This is where A′'s final architecture lands.

**2E — connected flow verification.** One malformed athlete row: dashboard/history →
degraded response; prescription path → `canonical_state_invalid`; benchmark observation →
no state mutation; DB `engine_state` → unchanged. The load-bearing proof that permissive
state cannot cross into decision authority.

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

## GATE 1 — production state sweep (PRECONDITION for 2B, not a post-check)

Requires real DB access. **Not runnable from an agent session — the user runs this.**

Build a **read-only** audit command that uses the **committed strict codec as the
classifier** rather than reproducing its required-field logic in hand-written SQL (two
implementations of "valid" will diverge, and the SQL copy is the one that will be wrong).
It must: select row identity, athlete identity, `engine_state`, and legacy-field presence;
run the strict codec **without writing**; classify typed outcomes; join only aggregate
recent-activity indicators; print counts and row identifiers, **never raw payloads**.

Classifications: `valid_current`, `null_engine_state_legacy_row`, `empty_vectors`,
`partial_vectors`, `malformed_current`, `nonfinite_value`, `missing_or_invalid_version`,
`unsupported_future_version`.

Output: row count · distinct athlete count · active in last 30/90 days · recent prescription
count · recent workout count · recent benchmark count · most recent activity.

Emit only: `athlete_state_id`, `athlete_id`, payload hash (`payload_hash()`), declared
version, normalized codec error, activity summary. **Never log raw `engine_state`** — it is
athlete data.

### Deployment gates by class

- **`engine_state IS NULL`** — a legacy-migration population, not necessarily corruption.
  These rows legitimately bootstrap from legacy scalars today; under strict they refuse at
  every decision surface. Fresh rows are safe (`state_bridge.py:253` writes `engine_state`).
  If nonzero: **backfill through the exact versioned legacy reconstruction before strict
  deployment**, and mark the result compatibility-derived in an audit record or provenance
  field. Do not pretend the lossy scalar projection recreated an originally observed vector.
- **Empty or partial vectors** — any *active* athlete in this class **blocks 2B**. Required
  sequence: inspect → repair through the forensic path → validate through the strict codec →
  compare-and-swap → then deploy strict decision loading.
- **Future versions** — never repair or reconstruct. They indicate incompatible deployment
  ordering: the older node must refuse, the payload must remain untouched, and the writer
  rollout must stop until all readers support the version.
- **Zero affected active athletes** — proceed with 2B once GATE 2 is closed.

## GATE 2 — e1RM transaction ownership — **CLOSED 2026-07-15. FALSE ALARM.**

**Observations are durable. There is no data loss. 2B3 is not blocked by this.**

Executed against real Postgres: `tests/test_observation_durability.py`, 3 passed.

`create_observation` **commits at `benchmark_service.py:428`**, after resolving capacity
authority and applying weak-point feedback. The `db.add` + `flush` at :312-313 is
mid-function — it exists to get `obs.id` for the downstream authority work — and the commit
lands ~115 lines later.

**How the false alarm happened, so it doesn't recur:** the original trace read to the flush
at :313 and stopped, concluding "no commit" from a partial read of a ~170-line function.
That conclusion was then repeated downstream without re-checking, and hardened into a merge
gate. The premise it rested on (`flush()` is not durability; uncommitted transactions roll
back on close) was correct, and is pinned by `test_flush_alone_is_not_durability`. The
premise was never the problem — the unread 115 lines were. *Grep for the commit in the whole
function before claiming a function does not commit.*

### What IS true, and is pinned

`create_observation` **owns its own transaction**. It is a complete command that commits, not
a leaky helper mid-flush. The consequence is a design property, not a defect:

> A caller CANNOT compose it into a larger atomic unit. By the time it returns, the
> observation is committed; a later failure in the caller cannot roll it back.

`process_new_workout` commits the workout at `state_service.py:963`, then
`create_observation` commits the observation separately at :428. **Two transactions, not
one.** This matches the post-commit best-effort convention proven in W1-C2 (`ab858f6`), but
it means "observation exists ⇔ its state consequences are consistent" is NOT guaranteed by
the database. Any future work needing that atomicity must MOVE the boundary — adding a
commit is not the lever. `test_caller_cannot_roll_back_a_created_observation` pins this so
the next person to assume otherwise is told.

### Unrelated observation about the suite (downgraded, not a bug)

`http_client` overrides `get_db` with `yield async_db` (`conftest.py:222-223`) — one session,
held open by the fixture for the whole test, where production opens and closes one per
request. So DB tests generally observe writes through the session that made them. This is
factually true and worth knowing, but it caused no defect here: the path commits, so the
distinction never bit. Do not treat it as evidence of a bug on its own.

### Load-bearing persistence test — do NOT use the creating session

The false-green pattern: flush → test queries with the *same* session → row appears → test
passes → session closes → transaction rolls back.

1. Create a unique observation through the real API / top-level command path.
2. Let the request dependency and its `AsyncSession` exit **completely**.
3. Open a **new independent** session/connection.
4. Query by the unique observation identity.
5. Verify the observation, provenance columns, and applied-effect audit are durable.
6. Restart/recycle the application session and query again.

Also add the inverse: observation flush succeeds, later state transition fails → determine
whether the command contract requires both to roll back.

### Do NOT fix by committing inside `create_observation`

A deep helper must not seize transaction ownership. A `commit()` there could leave the
observation durable while the state update failed and the authority audit is incomplete —
and would make the helper unsafe inside any larger command. The boundary belongs at the
top-level command:

```python
async with db.begin():
    observation = await create_observation(...)
    await apply_observation_effect(...)
```

One atomic unit: observation exists ⇔ its canonical state/audit consequences are consistent.
Post-commit telemetry stays outside that transaction, per the convention proven in W1-C2
(`ab858f6`).

### Outcomes

- **Survives a new session** → ownership exists outside the traced functions. Document the
  exact owner and add a regression test so it cannot vanish in a refactor.
- **Disappears** → numerical/data-integrity blocker. Establish top-level ownership, add the
  fresh-session durability test, add the atomic rollback test, and audit recent observation
  volume against expected workout ingestion. Determine whether only e1RM observations
  disappear, or all observations through the service; whether an outer route commits some
  paths but not others; and whether **existing tests have been validating flushed,
  uncommitted rows**.

**Closed 2026-07-15.** No repair is needed, so there is nothing for strict-state changes to
mask. 2B3 waits on GATE 1 alone.

## Interaction with INT-05

INT-15 owns `state_bridge.py:204` and the `:206-225` branch. INT-05 owns `:227`
(`sync_legacy_from_vectors`). Adjacent diff hunks, separable concerns — INT-15 does not
block on INT-05. Note INT-05 is far larger than its original framing: the legacy scalar
*fields on `UnifiedStateVector`* feed `prescriber.py` and the constraint engine, not just
the web screens. See the INT-05 memory for the corrected blast radius.
