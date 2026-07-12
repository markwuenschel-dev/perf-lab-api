# Execution Contract: INT-05 — the structural-safety override must decide off components, not the lossy blend

**Status:** DELIVERED 2026-07-12 _(Design D1 under `structural_fatigue_safety_policy_v1`; branch `feat/int-05-safety-components`. Verified: 992 tests pass, ruff + pyright strict clean, no live `f_struct_damage` read in the prescriber. Retire-columns half remains out of scope — §20. PR open, not merged.)_

**Source:** candidate `INT-05` from the 2026-07-12 Repo Audit Swarm ledger (Data/Schema/Contract + Bug; design branch; priority +7; was `needs-human-decision`). Scoped to the **safety-read** half of the merged candidate (the severity-4 bug); the dual-storage/retire-columns half is deferred (§20).

---

## 1. Executive mission

The prescriber's hard-stop "Structural fatigue critical" Recovery override reads a **lossy blended scalar** (`state.f_struct_damage` — `structural + tendon + 0.15·grip + 0.1·tissue_avg`, clamped) instead of the authoritative structural fatigue component that sits on the same state object. Make the structural-safety decision consume the real `FatigueState` components, so "Structural fatigue critical" means structural fatigue is actually critical — not that grip + tissue noise summed past a threshold.

## 2. Current baseline

- **Branch/state:** `main` @ `86fc5c0` (INT-01 PR #122 + INT-02 PR #121 merged), clean. Branch this mission `feat/int-05-safety-components` from `main`.
- **Runs today:** full suite green; `ruff`/`pyright` strict clean. The target is a **pure function** — verifiable without Postgres.
- **The defect (read this run):**
  - `app/logic/prescriber.py:114` — `if state.f_struct_damage > 70.0:` → a `Recovery` `SessionCandidate` (`branch_id="safety_structural_damage"`, `is_safety_override=True`) with rationale "Structural fatigue critical ({f_struct_damage:.1f}%)".
  - `state.f_struct_damage` is **always the lossy blend**: `app/engine/state_bridge.py:156-159` `sync_legacy_from_vectors` computes `f_struct_combined = min(100, structural + tendon + 0.15·grip + 0.1·tissue_avg)`, and `unified_from_athlete_row:227` recomputes it on every read — so even when the authoritative `FatigueState` components are present, the prescriber reads the blend.
  - The components are on the **same** state object: `state.fatigue_f.structural / .tendon / .grip`. The prescriber already uses them at `prescriber.py:101` (`tendon > 55 or structural > 65` → the milder "Tissue Deload", `branch_id="safety_tendon_structural"`).
- **Coverage nuance (why this isn't a blind swap):** the additive blend fires the full Recovery on *jointly moderate* structural+tendon (e.g. 40+35=75>70) that neither line-101 threshold catches individually — and also lets grip/tissue noise contribute to a "structural" decision. Any replacement must consciously preserve, tighten, or loosen that coverage (see §8).

## 3. Strategic meaning

A severity-4 finding: a hard-stop *injury-safety* redirect deciding off a lossy projection when the precise signal is in hand. The fix consumes the deepest available representation at the decision boundary and makes the "structural critical" message honest — with no schema or contract change.

## 4. Scope

- Replace the `state.f_struct_damage > 70` trigger in `_safety_candidates` (`prescriber.py:84-140`) with component-based structural-safety logic per the §8 decision.
- Unit tests over `_safety_candidates` proving the component-based triggers fire (and don't) for the representative fatigue shapes, including the joint-moderate and grip-noise cases.

## 5. Non-goals

- **Not** retiring the legacy scalar columns (`f_struct_damage`, `c_nm_force`, …) or the `sync_legacy_from_vectors` mirror — the web frontend consumes them (§6); that is a separate connected-impact migration (§20).
- **Not** changing the `SessionCandidate` output schema, the API, or `state.py`/`athlete_state.py` fields.
- **Not** touching the milder line-101 "Tissue Deload" redirect (it already reads components correctly) beyond ensuring the two don't double-fire incoherently.
- **Not** the other lossy-mirror axes (capacity `c_nm_force` etc.) or `personalization_shadow_service` (shadow-only).
- **Not** broad cleanup of `prescriber.py`.

## 6. Blast-radius summary

Contained to `prescriber.py` + a test. The override's *output* (a `Recovery` `SessionCandidate`) is unchanged in shape; only its *trigger* changes.
- **Readers of the lossy blend** (mapped this run): `prescriber.py:114` (the live safety decision — in scope); `constraint_engine/context_builder.py:42` places it into `ConstraintContext.legacy` but **no constraint rule branches on it** (grep: single match, the write); `personalization_shadow_service.py:59` (shadow-only). So the prescriber is the only live safety consumer.
- **Why retire-columns is out of scope:** `web/src/perflab/screens/HistoryScreen.tsx` + `OverviewScreen.tsx` read the legacy scalars via `web/src/types.gen.ts` (API-exposed). Removing them is a cross-language migration (API schema + generated types + two screens) — deferred.

## 7. Contracts / seams involved

- **Fatigue representation (owner: `app/schemas/engine_vectors.py` `FatigueState`):** `structural`, `tendon`, `grip`, … the authoritative per-axis components.
- **Legacy mirror (owner: `app/engine/state_bridge.py` `sync_legacy_from_vectors`):** the lossy `f_struct_damage` blend — the source of the proxy. Unchanged by this mission (still needed for the web-facing columns).
- **Safety decision (owner: `app/logic/prescriber.py` `_safety_candidates`):** the seam being corrected.

## 8. Human decision — resolved (Design D1)

**Rejected A/B/C.** A (`structural + tendon > 130`) and C both retain an **unvalidated additive commensurability** — nothing establishes that 10 structural points equal 10 tendon points in a hard-stop, and the sum manufactures criticality (e.g. `S=79,T=52 → 131` fires while neither is individually critical; `S=30,T=100 → 130` with strict `>` does *not* fire despite maximal tendon; the `S=80,T=50=130` boundary is accidental). B loses tendon-critical and joint coverage.

**Locked — Design D1: explicit component bands + a conjunctive joint rule**, versioned:

```
structural_critical  = structural >= 80
tendon_critical      = tendon     >= 70
jointly_high         = structural >= 70 AND tendon >= 60
requires_recovery    = structural_critical OR tendon_critical OR jointly_high
```

- **No component is converted into another by addition.** Joint escalation requires *both* to cross explicit thresholds; one cannot arithmetically compensate for the other.
- **Inclusive (`>=`) and boundary-tested.** Values are **provisional expert-prior planning thresholds, not validated injury-risk cutoffs** — no single fatigue marker is a universal injury threshold (Soligard et al. 2016; Thorpe et al. 2017). Held in a named policy object so logs/tests/retunes identify the exact version:

```python
@dataclass(frozen=True)
class StructuralFatigueSafetyPolicy:
    structural_critical: float
    tendon_critical: float
    joint_structural_high: float
    joint_tendon_high: float
    version: str

STRUCTURAL_FATIGUE_SAFETY_POLICY_V1 = StructuralFatigueSafetyPolicy(
    80.0, 70.0, 70.0, 60.0, "structural_fatigue_safety_policy_v1")
```

- **Escalation margin over the softer redirect** (Tissue Deload `structural > 65 OR tendon > 55`) is visible, not an unrelated aggregate.

**Precedence + single-candidate invariant:** the critical Recovery branch is evaluated **before** Tissue Deload and **suppresses** it — at most one structural/tendon safety candidate is emitted (never both, leaving downstream ranking to guess). **Rationale is trigger-specific** (names the component/combination that actually fired) and records the policy version; `branch_id="safety_structural_damage"` is kept (analytics stability). No injury-*prediction* language.

**Counterfactual telemetry (provisional-threshold safety net):** record legacy-blend-trigger vs component-trigger + reason + structural/tendon/grip/tissue_avg + policy_version, classified `both | legacy_only | component_only | neither`, via the **existing observability seam only** (structured logging) — the legacy result is telemetry, it has **no veto** after the fix. Not a schema project (T3; may fold into T2 if trivial).

> **Lockable decision:** INT-05 removes the lossy `f_struct_damage` projection from the live structural/tendon safety decision. The replacement does **not** add structural and tendon. Under `structural_fatigue_safety_policy_v1`, Recovery triggers when structural ≥ 80, tendon ≥ 70, or (structural ≥ 70 AND tendon ≥ 60); otherwise the softer Tissue Deload is unchanged. The critical branch is evaluated first and suppresses Tissue Deload so exactly one structural/tendon candidate is emitted. Grip, tissue-average, and `f_struct_damage` have no authority over this decision. Thresholds are provisional planning thresholds, not injury cutoffs; legacy-vs-component decisions are observed counterfactually.

## 9. Implementation strategy

Design D1 (§8). Add `StructuralFatigueSafetyPolicy` (frozen dataclass) + `_V1` instance and a pure `_structural_recovery_trigger(structural, tendon, policy) -> str | None` returning `"structural_critical" | "tendon_critical" | "jointly_high" | None`. In `_safety_candidates`, replace the two structural/tendon blocks (`:101` Tissue Deload and `:114` `f_struct_damage` Recovery) with: evaluate the trigger first — if it fires, append the `Recovery` candidate (trigger-specific rationale + policy version) and **skip** Tissue Deload; else fall through to the unchanged Tissue Deload (`tendon > 55 or structural > 65`). Emit a counterfactual log (legacy blend vs component decision). Rejected: A/C (additive commensurability), B (coverage loss), and reading the blend at all.

## 10. Task graph

```
T1 (RED component-policy spec — full boundary + grip-invariance matrix)
  └─ T2 (versioned policy + precedence + single-candidate suppression → GREEN)
        └─ T3 (counterfactual legacy-vs-component telemetry via the logging seam)
```
_T3 may fold into T2 if the instrumentation is a single log call. Not a schema project._

## 11. Task-by-task plan

### T1 — RED component-policy specification
- **Depends:** none
- **Purpose:** pin Design D1 as failing tests before the change (boundary + grip-invariance).
- **Files:** `tests/test_prescriber_safety.py` `NEW`
- **Action:** build `UnifiedStateVector`s (via `build_unified_state_vector`) with targeted `fatigue_f`; assert the structural/tendon safety family from `_safety_candidates(state)` per the §16 matrix — component-critical (`S=85,T=0`; `S=0,T=75`), joint (`S=72,T=62`), inclusive boundaries (`S=70,T=60`→Recovery; `S=80,T=0`; `S=0,T=70`), non-triggers falling to Tissue Deload (`S=79,T=59`; `S=69,T=61`), **exactly one** structural/tendon candidate (`S=100,T=100`; `S=85,T=75` → no Recovery+Deload duplicate), and a **parameterized grip/tissue-invariance property** (fixed S,T; grip/tissue swept → identical decision).
- **Check:** the matrix assertions.
- **Verify:** `uv run pytest tests/test_prescriber_safety.py -q` → **fails** against current `f_struct_damage` logic (red proven, e.g. the grip=100 case currently mis-fires); `uv run ruff check tests/test_prescriber_safety.py` clean.
- **Risk/rollback:** test-only; delete the file.

### T2 — Versioned component policy + precedence
- **Depends:** T1
- **Purpose:** decide off `structural`/`tendon` components under `structural_fatigue_safety_policy_v1`, critical-before-milder, one candidate.
- **Files:** `app/logic/prescriber.py` (`_safety_candidates` `:101`+`:114`; add `StructuralFatigueSafetyPolicy` + `_V1` + `_structural_recovery_trigger`)
- **Action:** add the frozen policy dataclass + `_V1` instance + pure `_structural_recovery_trigger(structural, tendon, policy)` (structural_critical → tendon_critical → jointly_high → None, inclusive `>=`). Replace the two structural/tendon blocks: `trigger = _structural_recovery_trigger(...)`; if not None → append `Recovery` (`branch_id="safety_structural_damage"`, trigger-specific rationale naming the actual component(s) + policy version, no injury-prediction wording); `elif tendon > 55 or structural > 65` → the unchanged Tissue Deload. Nothing reads `f_struct_damage`/grip/tissue for this decision.
- **Check:** T1 green; existing prescriber suite green.
- **Verify:** `uv run pytest tests/test_prescriber_safety.py -q` green; `uv run pytest tests/ -q -k "prescrib or safety"` green; `grep -n f_struct_damage app/logic/prescriber.py` → no matches; `uv run ruff check app/logic/prescriber.py && uv run pyright app/logic/prescriber.py` clean.
- **Risk/rollback:** revert the block.

### T3 — Counterfactual legacy-vs-component telemetry
- **Depends:** T2
- **Purpose:** observe the provisional thresholds without giving legacy any veto.
- **Files:** `app/logic/prescriber.py` (a small `_log_structural_safety_counterfactual` helper called from `_safety_candidates`)
- **Action:** compute the legacy blend (`structural + tendon + 0.15·grip + 0.1·tissue_avg`) and its `>70` trigger, compare to the component trigger, classify `both|legacy_only|component_only|neither`, and `logger.info(...)` the fields (legacy_trigger, component_trigger, reason, structural, tendon, grip, tissue_avg, policy_version) on disagreement (INFO) else DEBUG. Structured logging only — **no schema, no new table**. Legacy is telemetry, never a veto.
- **Check:** a test asserting the counterfactual helper emits a `component_only` classification for the grip-noise case and does not alter the returned candidates.
- **Verify:** `uv run pytest tests/test_prescriber_safety.py -q -k counterfactual` green; `uv run ruff check app/logic/prescriber.py` clean.
- **Risk/rollback:** telemetry-only; remove the call.

## 12. Execution mode

**Sequential.** One pure function + a test; no schema, API, generated-artifact, fixture, or cross-language change (the retire-columns migration that *would* be connected-impact is out of scope). One agent, T1 then T2.

## 13. Required commands

```bash
uv run pytest tests/test_prescriber_safety.py -q
uv run pytest tests/ -q -k "prescrib or safety"
uv run ruff check .
uv run pyright
```

## 14. Verification gates

- **After T1:** the spec tests exist and **fail** against the current blend logic (red proven).
- **After T2:** spec tests green; the existing prescriber/safety suite green; `ruff`/`pyright` clean.
- **Final:** full `uv run pytest -q` green (no collateral).

## 15. Failure codes

```
FAIL-SCOPE-CREEP        — touched the legacy columns / mirror / web / API, or another lossy axis.
FAIL-PHANTOM-TARGET     — named a file absent from baseline and not marked NEW.
FAIL-UNVERIFIED-TASK    — reported done without the verify output.
FAIL-FAKE-GREEN         — the override still reads state.f_struct_damage.
FAIL-BURIED-DECISION    — thresholds chosen inside a task instead of from the §8 fork.
FAIL-SAFETY-COVERAGE-LOSS — a genuinely-high structural/tendon state no longer hard-stops.
```

## 16. Negative fixtures / boundary matrix (D1)

| structural | tendon | grip / tissue | expected | trigger |
|---|---|---|---|---|
| 85 | 0 | any | Recovery | structural_critical |
| 0 | 75 | any | Recovery | tendon_critical |
| 72 | 62 | any | Recovery | jointly_high |
| 79 | 59 | any | **no Recovery** → Tissue Deload eligible | — |
| 69 | 61 | any | **no Recovery** → Tissue Deload eligible | — |
| 70 | 60 | any | Recovery (inclusive boundary) | jointly_high |
| 80 | 0 | any | Recovery (inclusive) | structural_critical |
| 0 | 70 | any | Recovery (inclusive) | tendon_critical |
| 40 | 35 | grip=100, tissue=100 | **no** structural/tendon Recovery | — |
| 40 | 35 | grip=0, tissue=0 | same decision as the row above | — |
| 100 | 100 | any | **exactly one** Recovery candidate | structural_critical |
| 85 | 75 | any | exactly one Recovery, **no** Tissue-Deload duplicate | structural_critical |

**Grip/tissue invariance (property test):** for fixed `structural`/`tendon`, sweeping grip and tissue axes must not change the structural/tendon hard-stop decision.

## 17. Review plan

- **Spec axis:** the override reads `fatigue_f.structural`/`.tendon`, never `f_struct_damage`; the §8 thresholds are the ones implemented; the "structural critical" message reflects the actual component value; coverage change matches the approved design.
- **Quality axis:** thresholds are named constants; no duplicate-candidate emission with line 101; no widening of `_safety_candidates`' interface; the lossy mirror is untouched (still feeds the web columns).

## 18. Merge gate

Open the PR when: §13 commands green, full `uv run pytest -q` green, `ruff`/`pyright` clean, and the PR body records the §8 design + threshold values chosen (so the safety-appetite change is explicit). **Open PR and stop — do not merge.**

## 19. Definition of done

1. `uv run pytest tests/test_prescriber_safety.py -q` → green (structural-critical fires on real structural/joint; not on grip noise).
2. `uv run pytest tests/ -q -k "prescrib or safety"` → green (no regression).
3. `grep -n f_struct_damage app/logic/prescriber.py` → no matches (the proxy read is gone).
4. `uv run ruff check . && uv run pyright` → clean.

## 20. Follow-ups

- **Retire the legacy scalar columns** (`f_struct_damage`, `c_nm_force`, …) + the `sync_legacy_from_vectors` mirror — a connected-impact migration across the API schema, `web/types.gen.ts`, and `HistoryScreen`/`OverviewScreen`; blocked on the product decision "should the web read decomposed vectors instead?" (sibling INT-05 half; also relates to ADR-0007 legacy mirrors).
- **`engine_state` schema-evolution hardening** (INT-15) — adjacent to the mirror.
- **Audit other lossy-mirror reads** for decisions (capacity `c_nm_force`), if any surface — none live found this run.
