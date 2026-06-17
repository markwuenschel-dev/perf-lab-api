import type { BlockGoal } from "./types";

/**
 * Must match `TrainingGoal` in perf-lab-api `app/schemas/training_goals.py`
 * (used as `goal` query param for GET /v1/next-session).
 */
export const TRAINING_GOALS: readonly { value: string; label: string }[] = [
  { value: "Strength", label: "Strength" },
  { value: "Hypertrophy", label: "Hypertrophy" },
  { value: "Power", label: "Power (speed–strength)" },
  { value: "General", label: "General physical prep" },
  { value: "OlympicLifts", label: "Olympic weightlifting" },
  { value: "Powerlifting", label: "Powerlifting (SBD)" },
  { value: "MetCon", label: "Metabolic conditioning" },
  { value: "Calisthenics", label: "Calisthenics" },
  { value: "Gymnastics", label: "Gymnastics skills" },
  { value: "Grip", label: "Grip strength" },
  { value: "Running", label: "Running (base / easy)" },
  { value: "Sprinting", label: "Sprinting" },
  { value: "HalfMarathon", label: "Half marathon" },
  { value: "FullMarathon", label: "Full marathon" },
] as const;

export type TrainingGoalValue = (typeof TRAINING_GOALS)[number]["value"];

/**
 * Planning block goals — must match `BlockGoal` in perf-lab-api
 * `app/models/mesocycle.py` (used for POST /v1/planning/blocks).
 */
export const BLOCK_GOALS: readonly { value: BlockGoal; label: string }[] = [
  { value: "Strength", label: "Strength" },
  { value: "Hypertrophy", label: "Hypertrophy" },
  { value: "Power", label: "Power" },
  { value: "Hyrox", label: "Hyrox" },
  { value: "CrossFit", label: "CrossFit" },
  { value: "Running", label: "Running" },
  { value: "Calisthenics", label: "Calisthenics" },
  { value: "General", label: "General" },
  { value: "Recomp", label: "Recomp" },
] as const;
