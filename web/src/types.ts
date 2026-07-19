// src/types.ts
//
// API contract types are GENERATED from the backend's OpenAPI schema — see
// `types.gen.ts` (produced by `pnpm run gen:types`, which reads the backend's
// committed `openapi.json`). This module is a thin, hand-curated adapter that
// re-exports those generated shapes under the friendly names the app uses, plus
// the few frontend-only types that have no backend counterpart.
//
// Do NOT hand-edit API field shapes here. To change a field: edit the backend
// Pydantic schema, regenerate `openapi.json` + `types.gen.ts`, then fix whatever
// `tsc --noEmit` flags. See ../../docs/SYNC_WITH_BACKEND.md.

import type { components } from "./types.gen";

type Schemas = components["schemas"];

/* ---------- Auth ---------- */
export type TokenResponse = Schemas["TokenResponse"];
export type UserResponse = Schemas["UserResponse"];

/* ---------- Engine vectors / unified state ---------- */
export type CapacityState = Schemas["CapacityState"];
export type FatigueState = Schemas["FatigueState"];
export type TissueState = Schemas["TissueState"];
export type StressDoseSix = Schemas["StressDoseSix"];
export type UnifiedStateVector = Schemas["UnifiedStateVector"];

/* ---------- Workouts / dose ---------- */
export type Modality = Schemas["WorkoutLog"]["modality"];
export type WorkoutLog = Schemas["WorkoutLog"];
export type WorkoutSetEntry = Schemas["WorkoutSetEntry"];
export type StressDose = Schemas["StressDose"];

/* ---------- Exercise catalog (GET /v1/exercises) ---------- */
export type ExerciseCatalogOut = Schemas["ExerciseCatalogOut"];

/* ---------- Prescription ---------- */
export type ValidationSummary = Schemas["ValidationSummary"];
export type PrescriptionExplanation = Schemas["PrescriptionExplanation"];
export type ExercisePrescription = Schemas["ExercisePrescription"];
export type WorkoutPrescription = Schemas["WorkoutPrescription"];

/* ---------- Onboarding ---------- */
export type OnboardRequest = Schemas["OnboardRequest"];
export type OnboardResponse = Schemas["OnboardResponse"];
export type OnboardingStateResponse = Schemas["OnboardingStateResponse"];
export type OnboardingTwinSummary = Schemas["OnboardingTwinSummary"];
export type CompleteOnboardingRequest = Schemas["CompleteOnboardingRequest"];

/* ---------- Benchmark assessment surface (P10, ADR-0047) ---------- */
export type AssessmentSurfaceRead = Schemas["AssessmentSurfaceRead"];
export type AssessmentDomainGroup = Schemas["AssessmentDomainGroup"];
export type AssessmentBenchmarkCard = Schemas["AssessmentBenchmarkCard"];
export type BenchmarkObservationCreate = Schemas["BenchmarkObservationCreate"];

/* ---------- History (GET /v1/state-history, /v1/workouts) ---------- */
export type WorkoutLogSummary = Schemas["WorkoutLogSummary"];
export type BenchmarkObservationRead = Schemas["BenchmarkObservationRead"];

/* ---------- Athlete profile (GET / PATCH /v1/profile) ---------- */
export type ProfileRead = Schemas["ProfileRead"];
export type ProfileUpdate = Schemas["ProfileUpdate"];

/* ---------- Planning ---------- */
export type BlockGoal = Schemas["BlockGoal"];
export type BlockStatus = Schemas["BlockStatus"];
export type SessionStatus = Schemas["SessionStatus"];
export type WeeklyTemplateSlot = Schemas["WeeklyTemplateSlot"];
export type BlockCreateRequest = Schemas["BlockCreateRequest"];
export type BlockRead = Schemas["BlockRead"];
export type BlockUpdateRequest = Schemas["BlockUpdateRequest"];
export type PlannedSessionRead = Schemas["PlannedSessionRead"];
export type PlannedSessionUpdateRequest = Schemas["PlannedSessionUpdateRequest"];
export type TodaySessionResponse = Schemas["TodaySessionResponse"];

/* ---------- Wellness / readiness (P5, PDR-0005) ---------- */
export type WellnessSampleIn = Schemas["WellnessSampleIn"];
export type WellnessSampleOut = Schemas["WellnessSampleOut"];
export type ReadinessComponent = Schemas["ReadinessComponent"];
export type ReadinessScore = Schemas["ReadinessScore"];

/* ---------- Objectives (Phase 4a: benchmark-linked or free-text goals) ---------- */
export type ObjectiveCreate = Schemas["ObjectiveCreate"];
export type ObjectiveRead = Schemas["ObjectiveRead"];
export type ObjectiveUpdate = Schemas["ObjectiveUpdate"];
export type ObjectiveStatus = Schemas["ObjectiveStatus"];
export type ProgressBlock = Schemas["ProgressBlock"];

/* ---------- Dashboard overview (Phase 6: GET /v1/dashboard/overview) ---------- */
export type OverviewMetrics = Schemas["OverviewMetrics"];
export type TrainingLoadMetrics = Schemas["TrainingLoadMetrics"];
export type AdherenceMetrics = Schemas["AdherenceMetrics"];

/* ---------- Twin Simulator projection (Phase 7: POST /v1/simulate/projection) ---------- */
export type ProjectionRequest = Schemas["ProjectionRequest"];
export type ProjectionResponse = Schemas["ProjectionResponse"];
export type AxisProjection = Schemas["AxisProjection"];

/* ---------- Macrocycles (Phase 5: a program container anchored to an objective) ---------- */
export type MacrocycleCreate = Schemas["MacrocycleCreate"];
export type MacrocycleRead = Schemas["MacrocycleRead"];
export type MacrocycleUpdate = Schemas["MacrocycleUpdate"];
export type MacrocycleStatus = Schemas["MacrocycleStatus"];
export type WeekProgress = Schemas["WeekProgress"];

/* ---------- Wearable sync / Oura (Phase 2: /v1/integrations/oura/*) ---------- */
export type WearableConnectionOut = Schemas["WearableConnectionOut"];
export type ConnectionStatus = Schemas["ConnectionStatus"];
export type AuthorizeUrlResponse = Schemas["AuthorizeUrlResponse"];
export type PatConnectRequest = Schemas["PatConnectRequest"];
export type SyncResult = Schemas["SyncResult"];

/* ---------- Legacy field test (POST /compute-metrics, no /v1 prefix) ---------- */
// The backend schema is named `MetricsRequest`; the app calls it ComputeMetricsRequest.
export type ComputeMetricsRequest = Schemas["MetricsRequest"];
export type Zone = Schemas["Zone"];
export type MetricsResponse = Schemas["MetricsResponse"];

/* ========== Frontend-only types (no backend counterpart) ========== */

/** Normalized client error shape thrown by `perfLabClient`. */
export interface ApiError {
  message: string;
  status?: number;
  details?: unknown;
}

/**
 * Field Test → Digital Twin in-app handoff. The Field Test screen builds a
 * derived running session from the 1.5-mile test and hands it to the twin to
 * prefill the log form (see App wiring / DigitalTwinPanel).
 */
export interface FieldTestHandoff {
  log: Partial<WorkoutLog>;
  summary: string;
}
