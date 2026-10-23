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
| `repair_capacity_corruption.py` | **Strict** | ADR-0055 capacity job — appends canonical state |
| engine_state repair utility (**unbuilt**) | Raw forensic access | Must inspect the exact persisted payload |

> **These are two different tools; do not conflate them.** `repair_capacity_corruption.py`
> is the ADR-0055 capacity job. It is a canonical *mutation* path — it appends the
> athlete's newest state row — so it is strict (done in S4), and it needs no raw access.
> The forensic `read_raw_state_for_repair` utility named in `engine_state_codec.py` is
> **specified but unimplemented**; it is only needed if GATE 1 finds damaged rows. The
> allowlist once described the forensic tool on the capacity job's entry, because an AST
> scan matched the word "repair" — which is how a tool that launders reconstruction into
> canonical state got filed as one that reads raw payloads.

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
GATE 1  production sweep              ← CLEAR 2026-07-15 (1 athlete, 1 row, valid_current);
                                         RE-RUN REQUIRED before C1 deploys (see C1 below)
2B1  prescription                     ← DONE — first behaviour change on this branch
2B2  readiness                        ← DONE (readiness_service.py strict)
2B3  benchmark                        ← C1 (2026-10-21)
2B4  assessment                       ← C1 (2026-10-21); onboarding's twin summary → 2C
C1+  workout ingestion, projection    ← C1 (2026-10-21) — not in the original table
2C   dashboard / history / onboarding twin summary   ← C2 train (2026-10-21)
S3   shadows (separate slice)          ← C2 train (2026-10-21)
S4   repair_capacity_corruption -> strict   ← DONE, ungated (canonical mutation path)
2D   retire the temporary generic compatibility loader   ← C2 train (2026-10-21)
2E   connected-flow proof               ← C2 train (2026-10-21)
```

### C1 caller inventory (2026-10-21, AST scan of every loader call at 365f91e + C1)

Classified by what each caller can DO. "Decodes nothing" means it reads `has_state` (row
existence) only.

| Caller | Policy after C1 | Capability / reason |
|---|---|---|
| `benchmark_service._current_or_staged_baseline` | **Strict** (C1) | `benchmark`: the operator appends canonical state and the floor-shadow candidate |
| `benchmark_service` prior and fresh-athlete checks | Decodes nothing (C1) | existence only; a damaged row is an established athlete, never a fresh one |
| `state_service.process_new_workout` head read | **Strict** (C1) | `workout`: the transition is the next canonical row. Missing from the original table; it promoted legacy reconstructions into canonical state |
| `api/v1/simulate.py` (`/simulate/projection`) | **Strict** (C1) | `projection`: the athlete's own forecast is not display-only. It reached `load_or_init_current_state`, which the old guard did not match |
| `assessment_surface_service` | **Strict** (C1) | `assessment`: variance ranks the next benchmark |
| `api/v1/onboard.py` | Decodes nothing directly | stages a baseline only when no row exists; the B2 run reads no state; strength reports go through `stage_observation` as priors (`initialize_prior`), and a prior over ANY existing row is evidence-only via `has_state`, so it decodes nothing either (pinned: `test_onboarding_strength_over_a_damaged_head_is_evidence_only`) |
| `onboarding_service._twin_summary` | Permissive → **2C** | display only (a confidence label, `AssessmentSurfaceScreen.tsx:223-238`); `can_prescribe` does not read state. Must carry degraded provenance through the API and UI |
| `onboarding_service` measurement debt | Strict via assessment | refusal is swallowed to `[]` (prompts are never a gate) |
| `dashboard_service:391`, `api/v1/history.py:45` | Permissive → **2C** | display recovery with provenance |
| `ekf_shadow_service` ×3, `recovery_shadow_service` | Permissive → **S3** | explicit skip, no reconstruction |
| `state_service` internal decodes of rows it just wrote or already decoded strictly (`stage_baseline_state`; in `process_new_workout`: the record-only head and tail-replay head, post-commit `new_db_record`, post-e1RM re-read) | Permissive → **2D** | canonical by construction; pinned per function by `test_permissive_decodes_inside_state_service_are_pinned_per_function`; switched when the permissive loader is deleted |
| `initialize_athlete_state` race branch | **Strict by default** (C1) | only `load_or_init_current_state` (no app caller after C1; 2D) names the permissive decoder |
| `/v1/benchmarks/strength-evidence` | Strict via benchmark (C1) | measured and estimated reports both refuse over a damaged head; the report row, its submission key and the observation commit together or not at all |
| prescription, readiness, planning projection, week review, tail replay, forecast scoring, repair job | Strict (earlier slices) | unchanged |

C1's deploy is gated on a fresh sweep (`app/scripts/audit_engine_state.py`): every head must
decode, because each non-canonical head is refused at the four C1 capabilities. The sweep never
recommends backfilling canonical state from the legacy scalars — see its docstring.

**S4 is not a GATE 1 hedge.** It closes a laundering path in the ADR-0055 capacity job
(reconstruction copied, restamped v2, committed as canonical) and shrinks the allowlist by
one. It does nothing for a BLOCKED sweep: the forensic engine_state repair path is
unbuilt, and only the sweep can say whether it is needed.

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

### C2 train (2026-10-21): owner decisions and what landed

Owner answers: **1B 2A 3A** (history refuses the window; onboarding strict; keep `_strict`).

| Surface | Policy after C2 | Behaviour on a damaged head |
|---|---|---|
| `/v1/state-history` (`load_recent_state_snapshots`) | Display recovery | Each row carries `state_source` / `degraded` / `degradation_reason` / `codec_version`. A row unshowable even degraded (future version, unusable scalars) refuses the WHOLE window: 409 `display_state_unavailable`, `history_available=false`. Never omitted — Overview and Twin read the last row as current. No history stays 200 `[]`. |
| `/v1/dashboard/readiness` | Display recovery | Same provenance fields; 409 `dashboard_state_available=false` when unshowable. |
| Onboarding twin summary | **Strict** (2A) | `seeded=true`, `state_available=false`, `overall_confidence=null`; absent state is `seeded=false`; nothing initialized; still 200. A recovery has no measured confidence, so it is never shown as a band. |
| EKF and recovery shadows (S3) | `try_load_current_state_for_shadow` | Typed skip (`skipped_missing/incomplete/malformed/future_version`). Recovery classifies state BEFORE its unchanged-input shortcut and writes a skip row that supersedes an earlier computation; the evidence reader never presents that earlier computation as current. EKF writes no row (its newest row is the chain head) and logs the skip. |
| `state_service` internal decodes (2D) | Strict | Pre-commit decodes are of rows this request built or already decoded strictly. Post-commit, `_materialize_committed` keeps the valid result when a CONCURRENT writer's head is unreadable: never a failed save, a reconstruction or a reseed (pinned on ordinary, record-only and replay-corrected workouts). |
| Retired (2D) | Deleted | `unified_from_athlete_row`, `load_current_state`, `load_or_init_current_state`, `load_recent_states`, `_migrate_engine_state`. The permissive allowlist and per-scope pins are empty; a guard refuses any module that defines or re-exports them again. |

2E (`tests/test_int15_connected_flow.py`): one legacy row → display 200 degraded; onboarding
unavailable; six decision routes 409 with their own availability fields; the wellness
check-in saved with a recovery skip; reconstruction reached only by the two display
surfaces; the stored payload unchanged.

INT-05 C4 rides the same train: decision readers (prescriber, drivers, constraints) read the
canonical vectors; rule ids and `StateEvidence.axis` strings unchanged; the web strength
fallback to `c_nm_force` (a different scale) is removed. The scalars remain in API responses;
the drop migration still needs the reader audit and its own recheck.

Review round (2026-10-22), keeping 1B 2A 3A:

- **Damaged row columns are damaged state.** A payload that decodes on a row whose own
  `habit_strength` / `s_struct_signal` / `skill_state` cannot form a state (out of range, wrong
  type) raised a raw `ValidationError`/`ValueError` past every policy. `state_loading` now
  converts validation failures at the conversion boundary into
  `MalformedCurrentEngineState("invalid_row_fields")`: decisions 409, history refuses the
  window, onboarding is unavailable, shadows skip `skipped_malformed`, and the post-commit
  fallback keeps its result. Other exceptions are bugs and still propagate.
  Round 2: a NaN/±Infinity anywhere in the assembled state (validation accepts them from JSON
  strings such as `"NaN"`, in `skill_state` or in a payload vector) is
  `MalformedCurrentEngineState("non_finite_values")`, so no response carries one into
  serialization; and finite out-of-range legacy values (a negative capacity, fatigue or damage)
  make a recovery attempt `DisplayStateUnavailable(...+invalid_legacy_values)`, not a 500.
- **A recovery computation behind another sample's state skip is recomputed.** A state skip is
  athlete-wide in the evidence reader, so an unchanged sample's computed head that is older
  than it no longer satisfies a computed request (`wellness_shadow_chain._satisfies`); once
  state is readable again, evidence resumes.
- **Transition identity changes with this deploy.** The train edits `app/engine/state_bridge.py`
  and `app/schemas/state.py`, both in `transition_identity.TRANSITION_MODULES`, so
  `current_identity()` differs from the identity stamped on rows written before it. Under the
  existing policy a late event whose replay tail holds such a row is refused with
  `identity_mismatch` (`tail_replay_service`) and stays **record-only**: kept, not applied, and
  nothing corrupted. Backfills whose tail spans the deployment are therefore record-only. This
  is the accepted v1 behaviour for any edit to those modules.

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

## Failure mapping — **settled 2026-07-15, implemented in 2B1**

Three layers. Collapsing any two of them is the defect:

```
engine_state_codec      "this payload does not decode"      persistence
        │
        ▼   translated by the SERVICE that knows its own authority
CanonicalStateInvalid   "prescription must be refused"      product policy
        │
        ▼
global HTTP handler     409 + capability-specific body      transport
```

The raw `EngineStateDecodeError` is **never** a public contract. The codec knows only that
decoding failed — it cannot know whether the caller is prescription, readiness, benchmark
mutation, display recovery, a shadow, or an offline job, and those must not share an
outcome. Mapping it straight to 409 would make *forgotten translations look successful*,
concealing an accidental strict call from the wrong surface, a missing display adapter, or
a defect in a new route. An untranslated escape stays an **opaque 500** and logs
`untranslated_engine_state_decode_error` at ERROR — a defect alarm, not a contract
(`app/main.py`). Translation is written once per capability, in the service, not repeated
per route where one omission is the accidental 500 this decision forbids.

Internal (preserve precision for observability + repair): `canonical_state_missing`,
`canonical_state_incomplete`, `canonical_state_malformed`,
`canonical_state_version_unsupported`. See `normalize_decode_error` in `app/core/errors.py`.

External — **409 Conflict**. The request is well formed; resending it changes nothing. It
conflicts with the athlete's persisted state, which only an operator or repair workflow can
fix. (Not 503: nothing to retry. Not 422: FastAPI already uses it for request validation,
so clients would conflate a bad request with damaged stored state.)

```json
{ "error": "canonical_state_invalid",
  "prescription_available": false,
  "resolution_available_in_product": false }
```

Only the code and the status are shared across capabilities. The availability field is
per-capability (`readiness_available`, `benchmark_update_applied`, …) — forcing
`prescription_available` onto a readiness endpoint would be a lie dressed as consistency.

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
- **Zero affected active athletes** — proceed with 2B. GATE 2 is already closed, so the
  sweep is the only remaining gate.

### Result — **CLEAR, run 2026-07-15**

```
athletes with state : 1        valid_current  1
total state rows    : 1        historical rows: all valid
affected athletes   : none     VERDICT: CLEAR — 2B may proceed
```

**What this does and does not prove.** It clears 2B: the blast radius of strict refusal is
zero, and there are no `engine_state IS NULL` rows, so the attested-backfill requirement
above is moot. It is *not* evidence the codec is exercised against real-world payload
variety — it came back clear because production is essentially empty, with one athlete and
one row. The gate's question was "will strict loading refuse live athletes?", and the answer
is no. That is the only claim it supports.

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
