# Syncing with the Backend API

The frontend types in `src/types.ts` are manual mirrors of the backend Pydantic
schemas in `perf-lab-api/app/schemas/`. They are not generated automatically.

When the backend adds or renames a field, `src/types.ts` must be updated manually.
This document exists to make that process predictable.

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
