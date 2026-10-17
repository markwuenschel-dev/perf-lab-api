// src/perflab/objectives.ts
//
// Non-component helpers for the Objectives feature, kept out of the screen file
// so exporting them doesn't trip react-refresh/only-export-components. Shared by
// ObjectivesScreen (the list) and OverviewScreen (the top-objective hero card).
import type { AssessmentSurfaceRead, ConfidenceStatus, DrivingObjectiveRead, ObjectiveRead } from "@/types";

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

/**
 * The athlete's ACTIVE objectives in their chosen DISPLAY order (`display_rank`,
 * ADR-0061). GET /v1/objectives already returns display order; the stable sort on
 * `display_rank` (unranked last, server order kept) only guards against a caller
 * handing in rows from somewhere else. Display order is not a weight — it never
 * says what drives training (that is GET /v1/objectives/driving).
 */
export function activeInDisplayOrder(objs: ObjectiveRead[]): ObjectiveRead[] {
  return objs
    .map((o, i) => ({ o, i }))
    .filter(({ o }) => o.status === "active")
    .sort((a, b) => {
      const ar = a.o.display_rank ?? Infinity;
      const br = b.o.display_rank ?? Infinity;
      return ar !== br ? ar - br : a.i - b.i;
    })
    .map(({ o }) => o);
}

/** Achieved / abandoned objectives — outside the display order, never reorderable. */
export function closedObjectives(objs: ObjectiveRead[]): ObjectiveRead[] {
  return sortObjectives(objs.filter((o) => o.status !== "active"));
}

/** `ids` with the element at `from` moved to index `to` (a new array; out-of-range → unchanged copy). */
export function moveId(ids: readonly number[], from: number, to: number): number[] {
  const next = [...ids];
  if (from < 0 || from >= next.length || to < 0 || to >= next.length || from === to) return next;
  const [picked] = next.splice(from, 1);
  next.splice(to, 0, picked);
  return next;
}

/** The generated confidence band (never hand-declared — types.confidence.test.ts), or null
 *  when the benchmark has never been measured. */
export type EvidenceStatus = ConfidenceStatus | null;

/**
 * Benchmark code → the assessment surface's `confidence_status`, for the linked-benchmark
 * evidence chip. A code absent from the map is not in the (domain-filtered) surface, which
 * is different from a present code with a null status (never measured).
 */
export function evidenceByBenchmarkCode(surface: AssessmentSurfaceRead | null): Map<string, EvidenceStatus> {
  const out = new Map<string, EvidenceStatus>();
  for (const group of surface?.groups ?? []) {
    for (const card of group.cards) out.set(card.code, card.confidence_status);
  }
  return out;
}

/** Where the driving objective came from (`GET /v1/objectives/driving`). */
export type DrivingSource = NonNullable<DrivingObjectiveRead["source"]>;

/**
 * What the Overview hero shows, from one successful (list, driving) pair.
 *
 * The server's selector (`resolve_driving_objective`) is the only authority on which
 * objective drives prescription. The client never sorts or picks: it only looks the
 * returned id up in the active list, and when it can't, it says so instead of
 * substituting another objective.
 *
 * - `none`: a successful empty list AND no driving objective. Only this earns "set a goal".
 * - `objective`: the driving id found in the list.
 * - `missing`: the driving id is not in the active list. `/driving` returns only an id and
 *   a source, so absence does not tell us the objective's status (achieved, abandoned, or
 *   created/changed between the two reads).
 * - `disagree`: no driving objective, yet the list has active objectives. The two reads
 *   saw different moments (an objective changed between them); retry, never "set a goal".
 */
export type HeroModel =
  | { kind: "none" }
  | { kind: "objective"; objective: ObjectiveRead; source: DrivingSource | null }
  | { kind: "missing"; source: DrivingSource | null }
  | { kind: "disagree" };

export function heroModel(active: readonly ObjectiveRead[], driving: DrivingObjectiveRead): HeroModel {
  const id = driving.objective_id;
  if (id == null) return active.length === 0 ? { kind: "none" } : { kind: "disagree" };
  const objective = active.find((o) => o.id === id);
  return objective
    ? { kind: "objective", objective, source: driving.source ?? null }
    : { kind: "missing", source: driving.source ?? null };
}

/** The hero's kicker for a found objective. Names the lever that chose it, never a
 *  claim that one objective controls all training (on the priority path taper comes
 *  from the nearest upcoming objective, not this one). */
export function heroKicker(source: DrivingSource | null): string {
  return source === "macrocycle_anchor"
    ? "Program objective"
    : source === "priority"
      ? "Highest-priority objective"
      : "Objective";
}
