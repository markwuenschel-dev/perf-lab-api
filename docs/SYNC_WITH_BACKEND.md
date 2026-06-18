# Syncing with the Backend API

The frontend types in `src/types.ts` are manual mirrors of the backend Pydantic
schemas in `perf-lab-api/app/schemas/`. They are not generated automatically.

When the backend adds or renames a field, `src/types.ts` must be updated manually.
This document exists to make that process predictable.

---

## Redesign (Perf Lab "Performance OS") → Backend Wiring Plan

> **Status:** the redesigned UI (`src/perflab/`) currently runs entirely on the
> client-side simulation in `src/perflab/sim.ts` — **no backend calls**. This
> section is the plan for re-wiring the four screens that map to real endpoints.
> The per-component references *later in this document* (`StateSnapshot`,
> `LogWorkoutForm`, `HeroFlowColumn`, `OnboardingForm`, `PlanningPanel`,
> `DigitalTwinPanel`, `NextSessionCard`, `stateUtils.ts`, `trainingGoals.ts`) are
> **pre-redesign and have been removed** — they are superseded by `src/perflab/`.
> `src/types.ts` and `src/api/perfLabClient.ts` are unchanged (parked) and the
> client already has a typed function for every endpoint below.

### Backend reality (probed live 2026-06-18, `https://perf-lab-api.onrender.com`)

- `/ping` → `{"version":"0.2.0","project":"Performance Lab API"}`. **Backend is
  v0.2.0**; the UI labels say "S(t) · v0.3" — cosmetic, reconcile later.
- `GET /openapi.json` → **500** (backend schema-gen bug; `/docs` HTML loads but
  the spec doesn't). Until fixed, treat `types.ts` + live probes as the source of
  truth.
- Routes confirmed to exist: `POST /compute-metrics` (no auth), `POST
  /auth/register`, `POST /auth/token`, `GET /auth/me` (auth), `POST /v1/onboard`,
  `POST /v1/log-workout` (auth), `POST /v1/simulate-dose`, `GET /v1/next-session`
  (auth), `GET /v1/planning/{today,blocks,sessions}` (auth).
- **No GET current-state route** — `/v1/state`, `/v1/twin`, `/v1/me`,
  `/v1/state/current` all return **404**. A `UnifiedStateVector` is only returned
  as the **response body of `POST /v1/log-workout`**. ⇒ see Gap #1.

### 1. Field Test → `POST /compute-metrics` (unauthenticated) — ✅ fully backable

Request (`ComputeMetricsRequest`):

| UI input (`FieldTestScreen`) | Field | Notes |
|---|---|---|
| 300 m time | `time_300m` | string `"M:SS"` |
| 1.5 mi time | `time_1p5mi` | string `"MM:SS"` |
| Age | `age` | number |
| Sex | `sex` | `"Female"` / `"Male"` |

Response (`MetricsResponse`, verified against a live call):

| UI element | Field | Notes |
|---|---|---|
| VO₂max value | `vo2_max` | currently hardcoded `58.4` |
| VO₂ category caption | `vo2_category` | e.g. `"Above Average"` |
| "Speed ↔ Endurance" value | `fatigue_percent` | negative = endurance-biased |
| "endurance-biased" tag | `fatigue_profile` | label string |
| Pace zones Z1–Z5 | `zones[0..4]` | `name` + `slow_pace_sec`/`fast_pace_sec` |
| (not shown) | `race_pace_sec_per_mile` | |

⚠️ **Units:** backend zone paces are **seconds per *mile***; the UI labels zones
"min/km". Either relabel to `/mi` or convert (`÷ 1.609`). Backend zone names
(`Easy / Recovery`, `Steady State`, `Tempo`, `Interval / VO₂`, `Fast Repeats /
Speed`) → Z1–Z5; the UI also shows a short tag (recovery/endurance/tempo/
threshold/VO₂max). "Send to Twin" has no distinct seed endpoint — see Gap #1.

### 2. Onboarding → `/auth/register` + `/auth/token` + `POST /v1/onboard`

`OnboardRequest` is **strength-oriented**; the redesign onboarding is
**running-oriented** — partial mismatch:

| UI field (`OnboardingScreen`) | `OnboardRequest` field | Notes |
|---|---|---|
| (missing) email + password | `email` + register/login | UI has **no auth fields yet** — must add |
| Primary sport | `goal` | map sport → goal (Distance running → `"Running"`) |
| Weekly volume | — | no direct field |
| 1.5 mi seed | `run_5k_seconds` (approx) | or omit; onboard seeds baseline state |
| First/last name, DOB, sex, units | — | **not in `OnboardRequest`** (frontend-only profile) |
| — | `experience_level`, `experience_years`, `available_days_per_week`, `equipment[]`, `self_reported_weak_points[]`, 1RMs, `bodyweight_kg` | not collected by the running UI; send defaults / omit |

**Decision:** either (a) extend the UI to collect auth + the key `OnboardRequest`
fields, or (b) ask the backend for a running-profile onboard variant.

### 3. Digital Twin (+ Log Workout overlay) → `UnifiedStateVector`

| UI (`TwinScreen`) | Backend field | Notes |
|---|---|---|
| Fatigue F(t) — 6 bars | `fatigue_f` {cns,muscular,metabolic,structural,tendon,grip} | exact 6-axis match; **confirm 0–100 vs 0–1 scale** |
| Tissue T(t) — 8 regions | `tissue_t` {shoulder,elbow,wrist,lumbar,hip,knee,ankle,finger} | exact 8-region match |
| Capacities X(t) — 5 bars/radar | `capacity_x` | UI 5 ⊂ backend 8: aerobic→`aerobic`, glyco→`glycolytic`, strength→`max_strength`, power→`power`, workcap→`work_capacity` (omits `hypertrophy`/`skill`/`mobility`) |
| Readiness ring / score | **derived (frontend)** | `100 − 0.55·mean(F) − 0.45·max(T)` — backend has no readiness field |
| Struct. signal | `s_struct_signal` | |
| Habit | `habit_strength` | |
| Skill-state bars | `skill_state` (`Record<string,number>`) | map by keys present; current UI labels are placeholders |
| VO₂max + Profile tiles | from **last field test** (`MetricsResponse`), *not* the state vector | cache the last `compute-metrics` result |
| Time-travel slider + 22-day sparkline | **no backend** | needs a state-history endpoint — see Gap #2 |

Log Workout overlay:

| UI | Endpoint | Notes |
|---|---|---|
| Projected dose D(t) — 6 bars (preview) | `POST /v1/simulate-dose` → `StressDose.dose_six` {volume,intensity,density,impact,skill,metabolic} | **exact 6-axis match** |
| "Apply to twin →" | `POST /v1/log-workout` → new `UnifiedStateVector` | cache the response as current state (Gap #1 workaround) |
| chips / duration / distance / pace / RPE | build a `WorkoutLog` | `modality:"Running"`, `duration_minutes`, `session_rpe`, `distance_meters`, + required `sleep_quality`, `life_stress_inverse` |
| Resulting S(t) shift | diff cached-current vs returned state | |

### 4. Planning → `/v1/planning/*` + `/v1/next-session`

| UI (`PlanningScreen`) | Endpoint / field | Notes |
|---|---|---|
| "This week" 7-day strip | `GET /v1/planning/sessions?start_date&end_date` → `PlannedSessionRead[]` | `scheduled_date`, `day_of_week`, `category`, `modality`, `status` |
| "Block · Mid-base wk 3/7" (+ sidebar block card) | `GET /v1/planning/blocks` → active `BlockRead` | `goal`, `start_date`, `duration_weeks`, `sessions_per_week` |
| "Wednesday · prescribed" detail | `GET /v1/planning/today?goal=` → `TodaySessionResponse` {session, prescription} | or `GET /v1/next-session?goal=` → `WorkoutPrescription` |
| Stress dose D(t) bars (prescribed) | `POST /v1/simulate-dose` for the prescribed session | not part of the prescription payload |
| Readiness line on chart | **derived** | no backend series |
| Projected impact (after) | diff vs simulate-dose / cached state | derived |
| "Start session" → Session Player | frontend-only | no live-session backend |

### Gaps / decisions to resolve before (or alongside) implementation

1. **No current-state GET endpoint.** Twin + Overview readiness need `S(t)` on
   load. Options: (a) **recommend backend add `GET /v1/state`** (best); (b) cache
   the last `log-workout` response in `localStorage`; (c) seed from the onboard
   response. **Interim: (b), and request (a).**
2. **No state history / trends.** Twin 22-day time-travel and History
   readiness/load/VO₂ trends have **no backend source** → keep sim-only or add a
   history endpoint.
3. **Auth UI is missing.** Every `/v1/*` call needs a bearer token from
   `/auth/token`. The redesign has no login/register UI (the parked `auth/`
   context still exists). A minimal auth entry is a prerequisite for Twin/Planning.
4. **Scale/units to confirm:** `fatigue_f`/`tissue_t` 0–100 vs 0–1; zone paces
   sec/mile vs the UI's `/km` label.
5. **Capacity axes:** UI shows 5, backend has 8 — decide whether to surface all.
6. **Onboarding field mismatch** (running UI vs strength-oriented `OnboardRequest`).
7. **Version drift:** backend `0.2.0` vs UI "v0.3"; `/openapi.json` 500 (file a
   backend bug).

### Stays client-side (`sim.ts`) — no endpoints exist

Simulator (`buildProjection`), Goal Race (predicted-time math), History (trends),
and the live Session Player remain pure simulation.

### Suggested implementation order

1. **Auth shell** (login/register + token storage via parked `auth/`) — unblocks
   all `/v1/*`.
2. **Field Test** → `compute-metrics` (no auth; smallest real slice; cache the
   result to feed the Twin's VO₂/Profile tiles).
3. **Log Workout** → `simulate-dose` (preview) + `log-workout` (apply) — exact
   dose-axis match; cache the returned state.
4. **Twin** ← cached state (Gap #1) + cached field test; readiness derived.
5. **Planning** ← blocks / sessions / today.
6. Leave Simulator / Goal Race / History / Session Player on `sim.ts`.

---

## Type Mapping Table

| Frontend (`src/types.ts`) | Backend schema (`app/schemas/`) | Notes |
|---|---|---|
| `OnboardRequest` | `onboarding.py :: OnboardRequest` | All fields optional except `email` |
| `OnboardResponse` | `onboarding.py :: OnboardResponse` | |
| `WorkoutLog` | `workouts.py :: WorkoutLog` | See field notes below |
| `StressDose` | `workouts.py :: StressDose` | |
| `WorkoutPrescription` | `prescription.py :: WorkoutPrescription` | |
| `ExercisePrescription` | `prescription.py :: ExercisePrescription` | |
| `PrescriptionExplanation` | `prescription.py :: PrescriptionExplanation` | |
| `ValidationSummary` | `prescription.py :: ValidationSummary` | |
| `UnifiedStateVector` | `state.py :: UnifiedStateVector` | |
| `CapacityState` | `engine_vectors.py :: CapacityState` | |
| `FatigueState` | `engine_vectors.py :: FatigueState` | |
| `TissueState` | `engine_vectors.py :: TissueState` | |
| `StressDoseSix` | `engine_vectors.py :: StressDoseSix` | |
| `Modality` | `workouts.py :: Modality` (Literal union) | Must match exactly — see below |
| `BlockGoal` / `BlockStatus` / `SessionStatus` | `mesocycle.py` enums | Planning layer status/goal values |
| `BlockCreateRequest` / `BlockRead` / `BlockUpdateRequest` | `planning.py` schemas | `/v1/planning/blocks*` |
| `PlannedSessionRead` / `PlannedSessionUpdateRequest` | `planning.py` schemas | `/v1/planning/sessions*` |
| `TodaySessionResponse` | `planning.py :: TodaySessionResponse` | `/v1/planning/today` |
| `ComputeMetricsRequest` / `MetricsResponse` / `Zone` | `app/api/v1/legacy.py` | `POST /compute-metrics` — **legacy router, no `/v1` prefix** |
| `BLOCK_GOALS` (`src/trainingGoals.ts`) | `mesocycle.py :: BlockGoal` enum | Labeled list for the planning block dropdown |
| `FieldTestHandoff` | _frontend-only_ | Field Test → Twin prefill payload; not a backend schema |

---

## Fields the Frontend Intentionally Ignores

These fields are present in backend responses but not rendered in the UI.
They are still typed in `src/types.ts` (or should be) to avoid type errors
if a component ever accesses them.

| Field | Present in | Why ignored |
|---|---|---|
| `capacity_x` (decomposed) | `UnifiedStateVector` | `StateSnapshot` uses legacy scalar mirrors instead |
| `tissue_t` | `UnifiedStateVector` | Not currently visualized; used only in `readinessScore()` |
| `fatigue_f` | `UnifiedStateVector` | Used only in `readinessScore()`; not shown in `StateSnapshot` |
| `dose_six` | `StressDose` | Only the five legacy scalar channels are rendered in `DosePanel` |
| `adaptation_contribution` | `StressDose` | Not yet surfaced in the UI |
| `validation.failed_checks` | `PrescriptionExplanation` | Not rendered (only `warnings` shown) |
| `validation.hard_violations` | `PrescriptionExplanation` | Not rendered |
| `score` | `PrescriptionExplanation` | Not rendered |
| `template_id` | `PrescriptionExplanation` | Not rendered |
| `prescription_branch` | `PrescriptionExplanation` | Not rendered |
| `structured_template_name` | `PrescriptionExplanation` | Not rendered |
| `source_alignment` | `PrescriptionExplanation` | Not rendered |
| `planned_session_id` | `WorkoutLog` request | Internal linkage field; not directly rendered |
| `is_benchmark` / `benchmark_results` | `WorkoutLog` request | Only surfaced in benchmark flow UI |
| `slow_offset_sec` / `fast_offset_sec` | `Zone` (`MetricsResponse`) | Only absolute paces are rendered in `HeroFlowColumn` |

---

## The `Modality` Literal Union

```typescript
// src/types.ts
export type Modality = "Running" | "Strength" | "Hypertrophy" | "Power" | "Mixed";
```

This must exactly match the backend:
```python
# app/schemas/workouts.py
Literal["Running", "Strength", "Hypertrophy", "Power", "Mixed"]
```

If the backend adds a new modality (e.g. `"CrossFit"`), `src/types.ts` must be
updated and the `LogWorkoutForm.tsx` Select options must be extended.

---

## The `TrainingGoal` Enum

```typescript
// src/trainingGoals.ts
export const TRAINING_GOALS = [
  { value: "Strength", label: "Strength" },
  { value: "Hypertrophy", label: "Hypertrophy" },
  // ...
]
```

This must match:
```python
# app/schemas/training_goals.py
class TrainingGoal(str, Enum): ...
```

The comment at the top of `trainingGoals.ts` says exactly this. When the backend
adds a goal, add it to both the `TRAINING_GOALS` array and `OnboardingForm.tsx`'s
goal dropdown (it already uses `TRAINING_GOALS` so only one change is needed).

---

## How to Update When the Backend Changes a Schema

### Adding a new field to a response type

1. Add the field to the corresponding interface in `src/types.ts`
2. If it should be rendered, update the relevant component
3. If it's intentionally ignored, add it to the "Fields the Frontend
   Intentionally Ignores" table above

### Adding a new request field to `WorkoutLog` or `OnboardRequest`

1. Add the field to `src/types.ts`
2. Update the relevant form component to collect it
3. Update `toApiWorkoutLog()` in `stateUtils.ts` if it needs special
   serialization (e.g. stripping zeros, defaulting undefined)

### Adding a planning schema or endpoint

1. Add new interfaces to `src/types.ts` (planning section)
2. Add method(s) to `src/api/perfLabClient.ts`
3. If user-facing, wire to `PlanningPanel.tsx` and/or `DigitalTwinPanel.tsx`
4. Run `npx tsc --noEmit` and verify no prop/type drift

### Changing a field name

1. Update `src/types.ts`
2. `npx tsc --noEmit` will surface every broken reference

### Changing a Literal / Enum

1. Update the type in `src/types.ts`
2. Update any form Select options that use the enum values

---

## Checklist for Keeping Types in Sync

Run this after any backend schema change:

```bash
# 1. Type-check — catches missing/renamed fields used anywhere in components
npx tsc --noEmit

# 2. Verify the ignored-fields list in this document is still accurate

# 3. Verify Modality and TrainingGoal match the backend enum definitions

# 4. If new fields were added to WorkoutPrescription, check NextSessionCard.tsx
#    If new fields were added to UnifiedStateVector, check StateSnapshot.tsx
#    If new fields were added to OnboardRequest, check OnboardingForm.tsx
#    If planning schemas changed, check PlanningPanel.tsx + DigitalTwinPanel.tsx
```

---

## Historical Gap: How `model_version` and `exercises` Were Missed

The backend added `model_version` and `exercises` to `WorkoutPrescription` in
the v0.3 hardening pass, and `model_version` to `UnifiedStateVector`. The
frontend types were not updated at the same time.

This caused the frontend to silently ignore those fields — no TypeScript error
because optional fields in interfaces don't raise errors when absent from the
response. The gap was caught during the v0.3 frontend sync and fixed.

**Lesson:** When a backend field is required (no `Optional`), TypeScript will
catch it immediately. When it's optional (has a default), TypeScript won't
complain even if the frontend type doesn't declare it. Review optional additions
manually during backend schema changes.
