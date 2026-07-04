// src/perflab/macrocycles.ts
//
// Non-component helpers for the Macrocycle (program) feature, kept out of the
// screen/overlay files so exporting them doesn't trip
// react-refresh/only-export-components. Shared by OverviewScreen (the real
// "week X of Y" header) and ObjectivesScreen (the Program section).
import type { MacrocycleRead, WeekProgress } from "@/types";

/** The athlete's current program: the first active macrocycle, else the first
 *  one returned (the list endpoint defaults to active). null when there is none. */
export function activeMacrocycle(macros: MacrocycleRead[] | null): MacrocycleRead | null {
  if (!macros || macros.length === 0) return null;
  return macros.find((m) => m.status === "active") ?? macros[0];
}

/**
 * Human "week X of Y" from a WeekProgress. For an open horizon (the anchor
 * objective has no target_date, so total_weeks is null) we drop the "of Y" and
 * show just "week N" rather than inventing a finish line.
 */
export function weekProgressLabel(wp: WeekProgress): string {
  if (wp.total_weeks != null) return `week ${wp.current_week} of ${wp.total_weeks}`;
  return `week ${wp.current_week}`;
}
