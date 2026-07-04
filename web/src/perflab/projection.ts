// src/perflab/projection.ts
//
// Goal-aware capacity projection for the Twin Simulator (Phase 7).
//
// This module is the *contract boundary*. The shapes below (ProjectionAxis /
// ProjectionResponse) are the FROZEN data contract the backend implements at
// `POST /v1/simulate/projection`. Until that endpoint + its generated type land,
// the screen renders from the local `placeholderProjection(params)` generator in
// this file — it produces plausibly goal-shaped data so the screen works fully
// standalone. The later integration step swaps the single `placeholderProjection`
// call in SimulatorScreen for a real `useAuthedResource(getSimulateProjection…)`;
// nothing else in the screen needs to change.
//
// Non-component exports live here (not in the screen file) so react-refresh stays
// happy.

// The projection data contract now lives in the generated OpenAPI types. These
// re-exports keep the local names (`ProjectionAxis`, `ProjectionResponse`,
// `ProjectionParams`) the screen already imports, but they resolve to the single
// source of truth in `@/types` (backend-owned). They match field-for-field:
// AxisProjection { key, label, start, projected, baseline, series, baseline_series }
// + ProjectionResponse { goal, weeks, axes, readiness_series, peak_fatigue }.
import type { AxisProjection, ProjectionRequest, ProjectionResponse } from "@/types";

/** One capacity axis, projected forward under the chosen plan. */
export type ProjectionAxis = AxisProjection;

/** Full projection response — exactly 8 axes + readiness/fatigue trajectory. */
export type { ProjectionResponse };

/** Request body mirror for `POST /v1/simulate/projection`. */
export type ProjectionParams = ProjectionRequest;

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

// ── The 8 canonical capacity axes (CapacityState in types.gen.ts) ────────────
// `start` seeds a plausible current value; `max` is a soft display ceiling only.
interface AxisMeta {
  key: string;
  label: string;
  short: string;
  start: number;
  max: number;
}

export const AXES: AxisMeta[] = [
  { key: "aerobic", label: "Aerobic", short: "Aero", start: 300, max: 420 },
  { key: "glycolytic", label: "Glycolytic", short: "Glyco", start: 55, max: 110 },
  { key: "max_strength", label: "Max strength", short: "Strength", start: 100, max: 170 },
  { key: "hypertrophy", label: "Hypertrophy", short: "Hyper", start: 55, max: 110 },
  { key: "power", label: "Power", short: "Power", start: 55, max: 110 },
  { key: "skill", label: "Skill", short: "Skill", start: 55, max: 105 },
  { key: "mobility", label: "Mobility", short: "Mobility", start: 55, max: 105 },
  { key: "work_capacity", label: "Work capacity", short: "Work cap", start: 55, max: 110 },
];

// ── Goal → per-axis emphasis (0 = untargeted, 1 = dominant) ──────────────────
// Every goal keeps a low baseline on all axes (general fitness carries over) and
// lifts the axes that discipline actually trains. Shaped so a strength goal grows
// strength/hypertrophy/power, a running goal grows aerobic/work-capacity, etc.
type Weights = Record<string, number>;

const BALANCED: Weights = {
  aerobic: 0.5, glycolytic: 0.5, max_strength: 0.5, hypertrophy: 0.5,
  power: 0.5, skill: 0.5, mobility: 0.5, work_capacity: 0.5,
};

const w = (over: Partial<Weights>): Weights => ({ ...{
  aerobic: 0.18, glycolytic: 0.2, max_strength: 0.2, hypertrophy: 0.2,
  power: 0.2, skill: 0.22, mobility: 0.22, work_capacity: 0.24,
}, ...over });

const GOAL_WEIGHTS: Record<string, Weights> = {
  General: BALANCED,
  Strength: w({ max_strength: 1.0, hypertrophy: 0.7, power: 0.55, work_capacity: 0.4, glycolytic: 0.3 }),
  Hypertrophy: w({ hypertrophy: 1.0, max_strength: 0.7, work_capacity: 0.5, power: 0.35 }),
  Power: w({ power: 1.0, max_strength: 0.7, glycolytic: 0.55, skill: 0.45, hypertrophy: 0.4 }),
  Powerlifting: w({ max_strength: 1.0, hypertrophy: 0.65, power: 0.5, skill: 0.4 }),
  OlympicLifts: w({ power: 1.0, max_strength: 0.8, skill: 0.7, mobility: 0.5 }),
  Calisthenics: w({ max_strength: 0.75, skill: 0.85, mobility: 0.7, hypertrophy: 0.55, work_capacity: 0.5 }),
  Gymnastics: w({ skill: 1.0, mobility: 0.85, max_strength: 0.7, power: 0.55 }),
  Grip: w({ max_strength: 0.85, work_capacity: 0.6, hypertrophy: 0.5 }),
  MetCon: w({ work_capacity: 1.0, glycolytic: 0.85, aerobic: 0.6, power: 0.45 }),
  Running: w({ aerobic: 1.0, work_capacity: 0.7, glycolytic: 0.45, mobility: 0.35 }),
  Sprinting: w({ power: 1.0, glycolytic: 0.85, max_strength: 0.6, skill: 0.5, aerobic: 0.3 }),
  HalfMarathon: w({ aerobic: 1.0, work_capacity: 0.75, glycolytic: 0.4, mobility: 0.35 }),
  FullMarathon: w({ aerobic: 1.0, work_capacity: 0.85, mobility: 0.4, glycolytic: 0.3 }),
};

const weightsFor = (goal: string): Weights => GOAL_WEIGHTS[goal] ?? BALANCED;

/**
 * Deterministic, plausibly goal-shaped projection. Every control visibly moves
 * the output: goal reweights which axes grow, weekly_volume/intensity scale the
 * magnitude, recovery trades adaptation for fatigue, and weeks sets the horizon
 * (gains approach a ceiling with diminishing returns).
 */
export function placeholderProjection(p: ProjectionParams): ProjectionResponse {
  const weeks = Math.max(1, Math.round(p.weeks));
  const baseVol = 48;
  const intF = p.intensity === "easy" ? 0.85 : p.intensity === "hard" ? 1.22 : 1.0;
  // Recovery lifts adaptation but the same emphasis under "minimal" recovery
  // both blunts gains and spikes fatigue.
  const recAdapt = p.recovery === "high" ? 1.12 : p.recovery === "minimal" ? 0.82 : 1.0;
  const recFatigue = p.recovery === "high" ? 0.78 : p.recovery === "minimal" ? 1.32 : 1.0;
  const volF = clamp(0.55 + 0.45 * (p.weekly_volume / baseVol), 0.4, 1.7);
  const tau = 5; // weeks — adaptation time-constant (diminishing returns)

  const weights = weightsFor(p.goal);

  const axes: ProjectionAxis[] = AXES.map((a) => {
    const emphasis = weights[a.key] ?? 0.2;
    // Peak relative gain a fully-emphasized axis can reach on this plan.
    const maxRelGain = 0.38 * emphasis * intF * recAdapt * volF;
    const series: number[] = [];
    const baseline_series: number[] = [];
    for (let week = 0; week <= weeks; week++) {
      const approach = 1 - Math.exp(-week / tau);
      series.push(Math.round(a.start * (1 + maxRelGain * approach) * 10) / 10);
      // "Maintain" plan: hold current capacity (no growth, no detraining).
      baseline_series.push(a.start);
    }
    return {
      key: a.key,
      label: a.label,
      start: a.start,
      projected: series[weeks],
      baseline: baseline_series[weeks],
      series,
      baseline_series,
    };
  });

  // ── Readiness trajectory + peak fatigue ────────────────────────────────────
  const stress = (volF * intF) / recAdapt;
  const steadyReady = clamp(Math.round(90 - (stress - 0.85) * 42), 25, 92);
  const baseReady = 74;
  const readiness_series: number[] = [];
  for (let week = 0; week <= weeks; week++) {
    const approach = 1 - Math.exp(-week / 3);
    readiness_series.push(clamp(Math.round(baseReady + (steadyReady - baseReady) * approach), 20, 100));
  }
  const peak_fatigue = clamp(Math.round(26 + (stress - 0.85) * 55 * recFatigue), 8, 96);

  return { goal: p.goal, weeks, axes, readiness_series, peak_fatigue };
}

// ── Small display helpers (goal-general; no running vocabulary) ──────────────

/** Human label for a goal value (falls back to the raw value). */
export function goalLabel(goal: string): string {
  return GOAL_LABELS[goal] ?? goal;
}

export const GOAL_LABELS: Record<string, string> = {
  General: "General",
  Strength: "Strength",
  Hypertrophy: "Hypertrophy",
  Power: "Power",
  Powerlifting: "Powerlifting",
  OlympicLifts: "Olympic lifts",
  Calisthenics: "Calisthenics",
  Gymnastics: "Gymnastics",
  Grip: "Grip",
  MetCon: "MetCon",
  Running: "Running",
  Sprinting: "Sprinting",
  HalfMarathon: "Half marathon",
  FullMarathon: "Full marathon",
};

/** Axis keys ordered by how strongly this goal emphasizes them (dominant first). */
export function dominantAxes(goal: string): string[] {
  const weights = weightsFor(goal);
  return [...AXES].sort((a, b) => (weights[b.key] ?? 0) - (weights[a.key] ?? 0)).map((a) => a.key);
}
