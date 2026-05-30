# Syncing the Frontend with the Backend API

## Purpose

The frontend types in `src/types.ts` are manual mirrors of backend Pydantic schemas in `perf-lab-api/app/schemas/`. They are not generated automatically.

When the backend adds, removes, or renames a field, the frontend must be updated manually.

This document describes how to keep the React/TypeScript app aligned with the FastAPI backend.

## Source of Truth

Backend source of truth:

```text
app/schemas/workouts.py
app/schemas/state.py
app/schemas/prescription.py
app/schemas/onboarding.py
app/schemas/planning.py
app/schemas/benchmarks.py
app/schemas/dashboard.py
app/schemas/training_goals.py
app/schemas/engine_vectors.py
```

Frontend mirrors:

```text
src/types.ts
src/trainingGoals.ts
src/api/perfLabClient.ts
```

## Type Mapping Table

| Frontend | Backend | Notes |
|---|---|---|
| `ApiError` | frontend-only | Normalized client error shape |
| `TokenResponse` | `auth.py :: TokenResponse` | `/auth/token` |
| `UserResponse` | `auth.py :: UserResponse` | `/auth/me`, `/auth/register` |
| `Modality` | `workouts.py :: WorkoutLog.modality` | Must match Literal union |
| `WorkoutLog` | `workouts.py :: WorkoutLog` | Client log payload |
| `StressDose` | `workouts.py :: StressDose` | Dose preview output |
| `StressDoseSix` | `engine_vectors.py :: StressDoseSix` | Six-axis dose |
| `UnifiedStateVector` | `state.py :: UnifiedStateVector` | State snapshot |
| `CapacityState` | `engine_vectors.py :: CapacityState` | `capacity_x` |
| `FatigueState` | `engine_vectors.py :: FatigueState` | `fatigue_f` |
| `TissueState` | `engine_vectors.py :: TissueState` | `tissue_t` |
| `WorkoutPrescription` | `prescription.py :: WorkoutPrescription` | Next session output |
| `ExercisePrescription` | `prescription.py :: ExercisePrescription` | Prescription exercise rows |
| `PrescriptionExplanation` | `prescription.py :: PrescriptionExplanation` | `why` object |
| `ValidationSummary` | `prescription.py :: ValidationSummary` | nested in `why` |
| `OnboardRequest` | `onboarding.py :: OnboardRequest` | Frontend currently includes `email`, backend uploaded schema does not require it |
| `OnboardResponse` | `onboarding.py :: OnboardResponse` | Includes `next_step` |
| `BlockCreateRequest` | `planning.py :: BlockCreateRequest` | Planning block creation |
| `BlockRead` | `planning.py :: BlockRead` | Planning block response |
| `BlockUpdateRequest` | `planning.py :: BlockUpdateRequest` | Block patch |
| `PlannedSessionRead` | `planning.py :: PlannedSessionRead` | Session response |
| `PlannedSessionUpdateRequest` | `planning.py :: PlannedSessionUpdateRequest` | Session patch |
| `TodaySessionResponse` | `planning.py :: TodaySessionResponse` | Today slot + prescription payload |
| `TRAINING_GOALS` | `training_goals.py :: TrainingGoal` | Must match exactly |

## Current Literal Unions

### Modality

Frontend:

```ts
export type Modality = "Running" | "Strength" | "Hypertrophy" | "Power" | "Mixed";
```

Backend:

```python
Literal["Running", "Strength", "Hypertrophy", "Power", "Mixed"]
```

If the backend adds modalities such as `Conditioning` or `Calisthenics`, update:

- `src/types.ts`
- `LogWorkoutForm.tsx` modality selector
- dose/prescriber docs
- any frontend validation or serialization helpers

### TrainingGoal

Backend values:

```text
Strength
Hypertrophy
Power
General
OlympicLifts
Powerlifting
MetCon
Calisthenics
Gymnastics
Grip
Running
Sprinting
HalfMarathon
FullMarathon
```

Frontend `TRAINING_GOALS` currently matches this set.

### BlockGoal

Planning block goals are not the same as prescription goals.

Current frontend/backend block goals:

```text
Strength
Hypertrophy
Power
Hyrox
CrossFit
Running
Calisthenics
General
Recomp
```

Do not blindly reuse `TrainingGoal` for `BlockGoal`.

## API Client Mapping

`src/api/perfLabClient.ts` currently maps:

Auth:

```text
POST /auth/register
POST /auth/token
GET  /auth/me
```

Health:

```text
GET /ping
```

Digital twin:

```text
GET  /v1/next-session
POST /v1/log-workout
POST /v1/simulate-dose
POST /v1/onboard
```

Planning:

```text
POST  /v1/planning/blocks
GET   /v1/planning/blocks
PATCH /v1/planning/blocks/{id}
GET   /v1/planning/sessions
PATCH /v1/planning/sessions/{id}
GET   /v1/planning/today
```

Not yet mirrored in the uploaded frontend API client:

```text
GET  /v1/benchmarks/definitions
POST /v1/benchmarks/observations
GET  /v1/benchmarks/observations
POST /v1/benchmarks/recompute-derived
GET  /v1/dashboard/kpis
GET  /v1/dashboard/domain-summary
GET  /v1/dashboard/readiness
```

Add these when building benchmark/dashboard UI.

## Known Sync Caveats

### OnboardRequest email

The frontend `OnboardRequest` includes `email: string`. The uploaded backend `OnboardRequest` does not include `email`; the authenticated user supplies identity through the token.

Recommended fix:

- remove `email` from frontend `OnboardRequest`, or make it optional and unused
- ensure `completeOnboarding()` does not rely on email being accepted by backend

### Onboard request fields vs route assignment

Backend schema includes lift/bodyweight/run fields. ORM profile supports many baseline fields. The uploaded onboarding route currently assigns only:

- experience years
- experience level
- available days per week
- session duration
- equipment

It uses `squat_1rm_kg` for baseline state seeding but does not assign all accepted baseline fields to the profile.

Recommended backend fix:

- persist `squat_1rm`, `deadlift_1rm`, `bench_1rm`, `bodyweight_kg`, and `run_5k_seconds` in the profile when provided.

### StressDose adaptation contribution

Backend `StressDose` includes `adaptation_contribution`. Frontend `StressDose` currently mirrors `dose_six` and legacy scalar channels, but not `adaptation_contribution`.

Recommended frontend fix:

- add `AdaptationContribution` type
- add `adaptation_contribution` to `StressDose`
- decide whether to render it or list it as intentionally ignored

### ExerciseEntry

Backend `WorkoutLog` supports `exercises: ExerciseEntry[]`. Frontend `WorkoutLog` currently does not type exercise entries.

Recommended frontend fix when exercise-level logging is built:

- add `ExerciseEntry` interface
- add `exercises: ExerciseEntry[]` to `WorkoutLog`
- update `LogWorkoutForm` or a future exercise builder UI
- update `toApiWorkoutLog()` serialization if present

### Benchmark and dashboard DTOs

Backend benchmark/dashboard schemas are implemented. Frontend mirror types and client functions are not included in uploaded `src/types.ts` / `perfLabClient.ts`.

Recommended future additions:

- `BenchmarkDefinitionRead`
- `BenchmarkObservationCreate`
- `BenchmarkObservationRead`
- `RecomputeDerivedResponse`
- `KPIValueOut`
- `AnchorObservationOut`
- `DashboardBundleOut`
- `DomainSummaryOut`
- `ReadinessOut`

## Fields Intentionally Ignored by UI

These backend fields are typed or should be typed even when not rendered.

| Field | Present in | Current UI status |
|---|---|---|
| `capacity_x` | `UnifiedStateVector` | used for readiness / typed |
| `fatigue_f` | `UnifiedStateVector` | used for readiness / typed |
| `tissue_t` | `UnifiedStateVector` | used for readiness / typed |
| `model_version` | `UnifiedStateVector`, `WorkoutPrescription` | typed/rendered in some places |
| `source_alignment` | `PrescriptionExplanation` | typed; detailed rendering may be limited |
| `template_id` | `PrescriptionExplanation` | typed; mostly diagnostic |
| `prescription_branch` | `PrescriptionExplanation` | typed; diagnostic |
| `validation` | `PrescriptionExplanation` | typed; detailed rendering may be limited |
| `score` | `PrescriptionExplanation` | typed; diagnostic |
| `structured_template_name` | `PrescriptionExplanation` | typed; diagnostic |
| `adaptation_contribution` | `StressDose` | backend present; frontend type missing in uploaded file |
| `exercises` in `WorkoutLog` | `WorkoutLog` | backend present; frontend type missing in uploaded file |

## How to Update When Backend Schemas Change

### Adding a new response field

1. Add it to `src/types.ts`.
2. If rendered, update the relevant component.
3. If not rendered, add it to the ignored-fields table above.
4. Run `npx tsc --noEmit`.

### Adding a new request field

1. Add it to `src/types.ts`.
2. Update the form component if the user should supply it.
3. Update serialization helper if any values should be stripped/defaulted.
4. Verify API client payload shape.

### Changing a field name

1. Update `src/types.ts`.
2. Run `npx tsc --noEmit`.
3. Fix every broken reference.
4. Search raw string references in components.

### Changing a literal or enum

1. Update backend enum/schema.
2. Update frontend type union.
3. Update dropdown/options components.
4. Update docs.
5. Run typecheck/build.

## Checklist After Backend Schema Change

```bash
npx tsc --noEmit
npm run build
npm run lint
```

Then inspect:

- `src/types.ts`
- `src/trainingGoals.ts`
- `src/api/perfLabClient.ts`
- `src/components/DigitalTwinPanel.tsx`
- `src/components/OnboardingForm.tsx`
- `src/components/PlanningPanel.tsx`
- relevant `src/components/twin/*` components

## Future Improvement

When schemas stabilize, consider generating TypeScript types from OpenAPI to reduce manual drift. Until then, manual sync is acceptable only if this checklist is followed consistently.
