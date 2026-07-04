// src/perflab/objectives.ts
//
// Non-component helpers for the Objectives feature, kept out of the screen file
// so exporting them doesn't trip react-refresh/only-export-components. Shared by
// ObjectivesScreen (the list) and OverviewScreen (the top-objective hero card).
import type { ObjectiveRead } from "@/types";

// Active-first, then lowest priority number (highest priority) first, then
// nearest days_to_go (nulls sort last within a priority tier).
export function sortObjectives(objs: ObjectiveRead[]): ObjectiveRead[] {
  const rank = (s: ObjectiveRead["status"]) => (s === "active" ? 0 : s === "achieved" ? 1 : 2);
  return [...objs].sort((a, b) => {
    if (rank(a.status) !== rank(b.status)) return rank(a.status) - rank(b.status);
    if (a.priority !== b.priority) return a.priority - b.priority;
    const ad = a.days_to_go ?? Infinity;
    const bd = b.days_to_go ?? Infinity;
    return ad - bd;
  });
}
