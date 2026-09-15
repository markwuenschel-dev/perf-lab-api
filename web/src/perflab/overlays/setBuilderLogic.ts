// src/perflab/overlays/setBuilderLogic.ts
//
// Pure helpers for the catalog-bound per-set entry (ADR-0045). Kept out of the
// component file so React Fast Refresh stays happy (component-only modules).
import type { ExerciseCatalogOut, Modality, WorkoutSetEntry } from "@/types";

/**
 * What the recommended workout asked for, carried on a pre-filled group. It is a TARGET,
 * never a reading: none of it is submitted until the athlete confirms the group or enters
 * a value of their own (see `isRecorded`).
 */
export type PrescribedTarget = {
  sets: number | null;
  /** The prescriber's reps string verbatim — "5", "3-5", "30-40 min conversational pace". */
  reps: string | null;
  loadKg: number | null;
  /** A ceiling for the set, not a reported effort — it is never copied into `rpe`. */
  rpeCap: number | null;
  note: string | null;
};

export type SetGroup = {
  key: number;
  exercise: ExerciseCatalogOut | null;
  freeText: string;
  loadType: string;
  count: number;
  reps?: number;
  loadKg?: number;
  distanceM?: number;
  durationS?: number;
  rpe?: number;
  band?: string;
  elevation?: string;
  /** Present only on a group pre-filled from the recommended workout. */
  target?: PrescribedTarget;
  /** The athlete confirmed or edited a pre-filled group. Meaningless without `target`. */
  confirmed?: boolean;
};

// Mirrors app/services/state_service._EXERCISE_TO_SESSION_MODALITY.
const EX_TO_SESSION: Record<string, Modality> = {
  Running: "Running", Strength: "Strength", Hypertrophy: "Hypertrophy",
  Power: "Power", Calisthenics: "Strength", Conditioning: "Mixed", Mixed: "Mixed",
};

export const LOADED = new Set(["barbell", "dumbbell", "kettlebell", "machine", "cable"]);

/**
 * Whether a group is part of what the athlete is logging. A group they added themselves
 * always is; a pre-filled one only once they confirm it or enter something — an untouched
 * recommendation is not a workout the athlete did.
 */
export function isRecorded(g: SetGroup): boolean {
  return !g.target || g.confirmed === true;
}

export function deriveModality(groups: SetGroup[]): Modality | null {
  const mods = new Set(
    groups
      .filter((g) => g.exercise && isRecorded(g))
      .map((g) => EX_TO_SESSION[g.exercise!.modality] ?? "Mixed"),
  );
  if (mods.size === 1) return [...mods][0];
  if (mods.size > 1) return "Mixed";
  return null;
}

/** The heaviest loaded group per exercise is the inferred top set (drives e1RM). */
export function topSetKeys(groups: SetGroup[]): Set<number> {
  const best = new Map<string, SetGroup>();
  for (const g of groups) {
    if (!isRecorded(g) || !g.exercise || !LOADED.has(g.loadType) || g.loadKg == null) continue;
    const id = String(g.exercise.id);
    const cur = best.get(id);
    if (!cur || (g.loadKg ?? 0) >= (cur.loadKg ?? 0)) best.set(id, g);
  }
  return new Set([...best.values()].map((g) => g.key));
}

export function groupsToSets(groups: SetGroup[]): WorkoutSetEntry[] {
  return groups
    .filter((g) => isRecorded(g) && (g.exercise || g.freeText.trim()))
    .map((g) => ({
      exercise_id: g.exercise?.id ?? null,
      exercise_name: g.exercise?.name ?? null,
      free_text_name: g.exercise ? null : g.freeText.trim() || null,
      load_type: g.loadType,
      sets: Math.max(1, g.count),
      reps: g.reps ?? null,
      load_kg: g.loadKg ?? null,
      distance_m: g.distanceM ?? null,
      duration_s: g.durationS ?? null,
      rpe: g.rpe ?? null,
      band: g.band || null,
      elevation: g.elevation || null,
    }));
}

/** A prescribed reps string that is one whole number ("5"), else null ("3-5", "30 min"). */
function wholeReps(reps: string | null): number | null {
  const m = reps?.match(/^\s*(\d+)\s*$/);
  return m ? parseInt(m[1], 10) : null;
}

/**
 * "Done as prescribed": the athlete's explicit statement that they did the target, which is
 * what makes copying it honest. Copies only what the target states unambiguously — the set
 * count, a single whole rep count, and the kg on a loaded movement. A rep range or a
 * time/pace string is left for the athlete to enter, and the RPE cap is never copied: a
 * ceiling is not an effort they reported.
 */
export function confirmAsPrescribed(g: SetGroup): SetGroup {
  if (!g.target) return g;
  const reps = wholeReps(g.target.reps);
  return {
    ...g,
    count: g.target.sets ?? g.count,
    ...(reps !== null ? { reps } : {}),
    ...(LOADED.has(g.loadType) && g.target.loadKg !== null ? { loadKg: g.target.loadKg } : {}),
    confirmed: true,
  };
}

/** The target as the athlete reads it: "5 × 3 @ 120 kg · RPE ≤ 8". */
export function describeTarget(t: PrescribedTarget): string {
  const load = t.loadKg !== null ? ` @ ${t.loadKg} kg` : "";
  const cap = t.rpeCap !== null ? ` · RPE ≤ ${t.rpeCap}` : "";
  return `${t.sets ?? "—"} × ${t.reps ?? "—"}${load}${cap}`;
}

let _nextKey = 1;
export function blankGroup(): SetGroup {
  return { key: _nextKey++, exercise: null, freeText: "", loadType: "reps", count: 3 };
}
