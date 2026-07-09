// src/perflab/overlays/setBuilderLogic.ts
//
// Pure helpers for the catalog-bound per-set entry (ADR-0045). Kept out of the
// component file so React Fast Refresh stays happy (component-only modules).
import type { ExerciseCatalogOut, Modality, WorkoutSetEntry } from "@/types";

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
};

// Mirrors app/services/state_service._EXERCISE_TO_SESSION_MODALITY.
const EX_TO_SESSION: Record<string, Modality> = {
  Running: "Running", Strength: "Strength", Hypertrophy: "Hypertrophy",
  Power: "Power", Calisthenics: "Strength", Conditioning: "Mixed", Mixed: "Mixed",
};

export const LOADED = new Set(["barbell", "dumbbell", "kettlebell", "machine", "cable"]);

export function deriveModality(groups: SetGroup[]): Modality | null {
  const mods = new Set(
    groups.filter((g) => g.exercise).map((g) => EX_TO_SESSION[g.exercise!.modality] ?? "Mixed"),
  );
  if (mods.size === 1) return [...mods][0];
  if (mods.size > 1) return "Mixed";
  return null;
}

/** The heaviest loaded group per exercise is the inferred top set (drives e1RM). */
export function topSetKeys(groups: SetGroup[]): Set<number> {
  const best = new Map<string, SetGroup>();
  for (const g of groups) {
    if (!g.exercise || !LOADED.has(g.loadType) || g.loadKg == null) continue;
    const id = String(g.exercise.id);
    const cur = best.get(id);
    if (!cur || (g.loadKg ?? 0) >= (cur.loadKg ?? 0)) best.set(id, g);
  }
  return new Set([...best.values()].map((g) => g.key));
}

export function groupsToSets(groups: SetGroup[]): WorkoutSetEntry[] {
  return groups
    .filter((g) => g.exercise || g.freeText.trim())
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

let _nextKey = 1;
export function blankGroup(): SetGroup {
  return { key: _nextKey++, exercise: null, freeText: "", loadType: "reps", count: 3 };
}
