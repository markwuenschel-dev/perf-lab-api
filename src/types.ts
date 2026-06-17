// src/types.ts

export interface ApiError {
  message: string;
  status?: number;
  details?: unknown;
}

/** POST /auth/token */
export interface TokenResponse {
  access_token: string;
  token_type: string;
}

/** GET /auth/me, POST /auth/register */
export interface UserResponse {
  id: number;
  email: string;
  is_active: boolean;
}

export type Modality = "Running" | "Strength" | "Hypertrophy" | "Power" | "Mixed";

export interface CapacityState {
  aerobic: number;
  glycolytic: number;
  max_strength: number;
  hypertrophy: number;
  power: number;
  skill: number;
  mobility: number;
  work_capacity: number;
}

export interface FatigueState {
  cns: number;
  muscular: number;
  metabolic: number;
  structural: number;
  tendon: number;
  grip: number;
}

export interface TissueState {
  shoulder: number;
  elbow: number;
  wrist: number;
  lumbar: number;
  hip: number;
  knee: number;
  ankle: number;
  finger: number;
}

export interface StressDoseSix {
  volume: number;
  intensity: number;
  density: number;
  impact: number;
  skill: number;
  metabolic: number;
}

export interface UnifiedStateVector {
  timestamp: string;

  capacity_x: CapacityState;
  fatigue_f: FatigueState;
  tissue_t: TissueState;

  // Capacities (legacy mirrors)
  c_met_aerobic: number;
  c_nm_force: number;
  c_struct: number;
  b_met_anaerobic: number;

  // Fatigues (legacy mirrors, 0–100)
  f_met_systemic: number;
  f_nm_peripheral: number;
  f_nm_central: number;
  f_struct_damage: number;

  s_struct_signal: number;

  habit_strength: number;
  skill_state: Record<string, number>;
  model_version: string;
}

export interface ValidationSummary {
  passed: boolean;
  failed_checks: string[];
  hard_violations?: string[];
}

export interface PrescriptionExplanation {
  state_drivers: string[];
  goal_alignment: string;
  constraints_applied: string[];
  source_alignment: string[];
  template_id?: string | null;
  prescription_branch?: string | null;
  validation?: ValidationSummary | null;
  warnings?: string[];
  score?: number | null;
  structured_template_name?: string | null;
}

export interface ExercisePrescription {
  name: string;
  sets?: number | null;
  reps?: string | null;
  load_note?: string | null;
  weak_point_tags: string[];
}

export interface WorkoutPrescription {
  type: string;
  focus: string;
  rationale: string;
  duration_min: number;
  model_version: string;
  exercises: ExercisePrescription[];
  why?: PrescriptionExplanation | null;
}

export interface WorkoutLog {
  timestamp: string;
  modality: Modality;

  duration_minutes: number;
  session_rpe: number;

  sleep_quality: number;
  life_stress_inverse: number;

  avg_rir?: number;
  distance_meters?: number;
  total_volume_load?: number;
  dominant_movement_pattern?: string;
  novelty?: number;
  estimated_sets?: number;
  planned_session_id?: number;
  is_benchmark?: boolean;
  benchmark_results?: Record<string, number>;
}

export interface StressDose {
  dose_six: StressDoseSix;
  d_met_systemic: number;
  d_nm_peripheral: number;
  d_nm_central: number;
  d_struct_damage: number;
  d_struct_signal: number;
}

/** POST /v1/onboard */
export interface OnboardRequest {
  email: string;
  experience_level?: string;
  experience_years?: number;
  available_days_per_week?: number;
  session_duration_minutes?: number;
  equipment?: string[];
  self_reported_weak_points?: string[];
  goal?: string;
  squat_1rm_kg?: number | null;
  deadlift_1rm_kg?: number | null;
  bench_1rm_kg?: number | null;
  bodyweight_kg?: number | null;
  run_5k_seconds?: number | null;
}

export interface OnboardResponse {
  user_id: number;
  profile_id: number;
  message: string;
  next_step: string;
}

export type BlockGoal =
  | "Strength"
  | "Hypertrophy"
  | "Power"
  | "Hyrox"
  | "CrossFit"
  | "Running"
  | "Calisthenics"
  | "General"
  | "Recomp";

export type BlockStatus = "active" | "completed" | "abandoned";
export type SessionStatus = "pending" | "completed" | "skipped" | "rescheduled";

export interface WeeklyTemplateSlot {
  day_of_week: number;
  category: string;
  modality: string;
}

export interface BlockCreateRequest {
  goal: BlockGoal;
  start_date: string;
  duration_weeks?: number;
  sessions_per_week?: number;
  weekly_template?: WeeklyTemplateSlot[];
  modality_mix?: Record<string, number>;
  rationale?: string;
  deload_every_n_weeks?: number;
  deload_volume_factor?: number;
  benchmark_every_n_weeks?: number;
}

export interface BlockRead {
  id: number;
  user_id: number;
  goal: BlockGoal;
  status: BlockStatus;
  start_date: string;
  end_date: string | null;
  duration_weeks: number;
  sessions_per_week: number;
  weekly_template: Record<string, unknown>[];
  modality_mix: Record<string, unknown>;
  rationale: string | null;
  deload_every_n_weeks: number;
  deload_volume_factor: number;
  created_at: string;
}

export interface BlockUpdateRequest {
  status?: BlockStatus;
  rationale?: string;
}

export interface PlannedSessionRead {
  id: number;
  block_id: number;
  user_id: number;
  scheduled_date: string;
  week_number: number;
  day_of_week: number;
  category: string;
  modality: string;
  status: SessionStatus;
  is_deload: boolean;
  is_benchmark: boolean;
  benchmark_key?: string | null;
  prescribed_content?: Record<string, unknown> | null;
  workout_log_id?: number | null;
  completed_at?: string | null;
}

export interface PlannedSessionUpdateRequest {
  status?: SessionStatus;
  scheduled_date?: string;
}

export interface TodaySessionResponse {
  session: PlannedSessionRead | null;
  prescription: Record<string, unknown> | null;
}

/** POST /compute-metrics (legacy router — no /v1 prefix) */
export interface ComputeMetricsRequest {
  age: number;
  sex: string;
  time_300m: string;
  time_1p5mi: string;
}

export interface Zone {
  name: string;
  slow_pace_sec: number;
  fast_pace_sec: number;
  notes: string;
  // slow_offset_sec / fast_offset_sec exist on the backend but are not rendered.
}

export interface MetricsResponse {
  vo2_max: number;
  vo2_category: string;
  result_category: string;
  fatigue_percent: number;
  fatigue_profile: string;
  race_pace_sec_per_mile: number;
  zones: Zone[];
}

/**
 * Field Test → Digital Twin handoff. The Field Test tab builds a derived
 * running session from the 1.5-mile test and hands it to the twin to prefill
 * the log form (see App.tsx wiring, HeroFlowColumn, DigitalTwinPanel).
 */
export interface FieldTestHandoff {
  log: Partial<WorkoutLog>;
  summary: string;
}
