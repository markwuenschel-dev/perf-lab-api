// src/perflab/overlays/blockCreateBody.ts
//
// The pure request body behind the create-block modal, kept out of the component so it can be
// tested without rendering — the house pattern (see workoutLogBody.ts, strengthEvidenceBody.ts).
import type { BlockCreateRequest, BlockGoal } from "@/types";

// BlockGoal is a smaller, block-scoped enum — NOT the 14-value athlete
// TRAINING_GOALS in store.tsx. Kept in sync with the `BlockGoal` schema
// (types.gen.ts); if the backend adds a value, add it here too.
export const BLOCK_GOALS: { value: BlockGoal; label: string }[] = [
  { value: "General", label: "General" },
  { value: "Strength", label: "Strength" },
  { value: "Hypertrophy", label: "Hypertrophy" },
  { value: "Power", label: "Power" },
  { value: "Hyrox", label: "Hyrox" },
  { value: "CrossFit", label: "CrossFit" },
  { value: "Running", label: "Running" },
  { value: "Calisthenics", label: "Calisthenics" },
  { value: "Recomp", label: "Recomp" },
];

// Secondary styles ride on `modality_mix`, the backend's existing weighted split (ADR-0030):
// the main style keeps MAIN_SHARE and the rest divide what's left. Values are canonical
// DOMAINS (app/logic/domains.py), not BlockGoal labels — the backend keys on the domain, and a
// day's `modality` label is lossy (powerlifting and strength both render "Strength").
export const SECONDARY_STYLES: { domain: string; label: string }[] = [
  { domain: "strength", label: "Strength" },
  { domain: "powerlifting", label: "Powerlifting" },
  { domain: "hypertrophy", label: "Hypertrophy" },
  { domain: "power", label: "Power" },
  { domain: "weightlifting", label: "Weightlifting" },
  { domain: "running", label: "Running" },
  { domain: "conditioning", label: "Conditioning" },
  { domain: "calisthenics", label: "Calisthenics" },
  { domain: "gymnastics", label: "Gymnastics" },
  { domain: "mixed", label: "Mixed modal" },
  { domain: "grip", label: "Grip" },
  { domain: "general", label: "General" },
];

/** The canonical domain a BlockGoal resolves to (app/logic/domains.py `canonical_domain`). */
export const GOAL_DOMAIN: Record<BlockGoal, string> = {
  General: "general",
  Strength: "strength",
  Hypertrophy: "hypertrophy",
  Power: "power",
  Hyrox: "mixed",
  CrossFit: "mixed",
  Running: "running",
  Calisthenics: "calisthenics",
  Recomp: "general",
};

/** The main style's share of the week when secondary styles are added. */
export const MAIN_SHARE = 0.6;

export type Intensity = "easy" | "medium" | "hard";
export const INTENSITIES: { value: Intensity; label: string; help: string }[] = [
  { value: "easy", label: "Easy", help: "Fewer working sets and a lower effort target." },
  { value: "medium", label: "Medium", help: "The standard progression for this block." },
  {
    value: "hard",
    label: "Hard",
    help: "Requests more work and higher effort. Readiness and other limits may reduce the session.",
  },
];

export type Emphasis = "minimal" | "balanced" | "high";
export const EMPHASIS: { value: Emphasis; label: string }[] = [
  { value: "minimal", label: "Minimal" },
  { value: "balanced", label: "Balanced" },
  { value: "high", label: "High" },
];

// Accessory focus tags the backend understands (`_ACCESSORY_BY_TAG`).
export const FOCUS_TAGS: { value: string; label: string }[] = [
  { value: "posterior_chain", label: "Posterior chain" },
  { value: "push", label: "Push" },
  { value: "pull", label: "Pull" },
  { value: "core", label: "Core" },
  { value: "single_leg", label: "Single-leg" },
];

const todayIso = (): string => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

const clamp = (n: number, min: number, max: number): number => (Number.isNaN(n) ? min : Math.min(max, Math.max(min, n)));

export interface BlockForm {
  goal: BlockGoal;
  secondary: string[]; // canonical domains, main style excluded
  intensity: Intensity;
  startDate: string;
  // Numeric fields are held as raw input text so they tolerate a transient blank
  // while retyping; they're parsed + clamped (with defaults) at submit time.
  durationWeeks: string;
  sessionsPerWeek: string;
  targetMinutes: string; // raw input text; "" → omit/null
  emphasis: Emphasis;
  focus: string[];
}

export function initialForm(): BlockForm {
  return {
    goal: "General",
    secondary: [],
    intensity: "medium",
    startDate: todayIso(),
    durationWeeks: "8",
    sessionsPerWeek: "3",
    targetMinutes: "",
    emphasis: "balanced",
    focus: [],
  };
}

/** Build the backend BlockCreateRequest from the form. Leaves weekly_template
 *  empty and modality_mix empty — the backend derives the template from goal
 *  + modality_mix (defaulted server-side when empty). */
function buildModalityMix(f: BlockForm): Record<string, number> {
  // No secondary styles → {} , which lets the backend use the goal's own default week.
  const others = f.secondary.filter((d) => d !== GOAL_DOMAIN[f.goal]);
  if (others.length === 0) return {};
  const share = (1 - MAIN_SHARE) / others.length;
  const mix: Record<string, number> = { [GOAL_DOMAIN[f.goal]]: MAIN_SHARE };
  for (const domain of others) mix[domain] = share;
  return mix;
}

export function buildBlockCreateRequest(f: BlockForm): BlockCreateRequest {
  const trimmed = f.targetMinutes.trim();
  const minutes = trimmed === "" ? null : clamp(Number(trimmed), 20, 180);
  return {
    goal: f.goal,
    start_date: f.startDate,
    duration_weeks: clamp(Number(f.durationWeeks.trim() || "8"), 1, 24),
    sessions_per_week: clamp(Number(f.sessionsPerWeek.trim() || "3"), 1, 7),
    weekly_template: [],
    modality_mix: buildModalityMix(f),
    intensity: f.intensity,
    target_session_minutes: minutes,
    accessory_emphasis: f.emphasis,
    accessory_focus: f.focus.length > 0 ? f.focus : null,
    deload_every_n_weeks: 4,
    deload_volume_factor: 0.6,
    benchmark_every_n_weeks: 4,
  } satisfies BlockCreateRequest;
}
