// src/perflab/overlays/prescriptionPrefill.ts
//
// Turns the workout the app is currently recommending into Log Workout groups. Pure and
// fixture-free (type imports only), so the modal's fetch code stays thin and this stays
// testable without a network.
//
// Which workout is "the current recommendation": when today has a pending planned session
// whose prescription is already stored, it is that stored prescription — the one the
// Overview card last showed — read back without re-prescribing. Otherwise the modal asks
// /v1/next-session with the athlete's own goal, the same request the Twin screen makes.
import type { ExerciseCatalogOut, ExercisePrescription, PlannedSessionRead } from "@/types";
import type { SetGroup } from "./setBuilderLogic";

/** Local calendar date as YYYY-MM-DD — the planned-session query speaks dates, not instants. */
export function isoLocalDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Today's pending planned session, lowest id first, or null. */
export function pickTodaysPendingSession(
  sessions: readonly PlannedSessionRead[],
  today: string,
): PlannedSessionRead | null {
  const pending = sessions
    .filter((s) => s.scheduled_date === today && s.status === "pending")
    .sort((a, b) => a.id - b.id);
  return pending[0] ?? null;
}

/**
 * The exercises of a stored prescription. `prescribed_content` is an untyped JSON column
 * (WorkoutPrescription.to_prescribed_content), so each entry is checked rather than cast:
 * anything without a string `name` is dropped, never guessed at.
 */
export function exercisesFromStoredPrescription(
  content: PlannedSessionRead["prescribed_content"],
): ExercisePrescription[] {
  const raw = content?.["exercises"];
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (e): e is ExercisePrescription =>
      typeof e === "object" && e !== null && typeof (e as { name?: unknown }).name === "string",
  );
}

/** A pre-filled group: the prescription rides along as `target`; no reading is set. */
export function plannedGroup(
  ex: ExercisePrescription,
  catalogMatch: ExerciseCatalogOut | null,
  key: number,
): SetGroup {
  return {
    key,
    exercise: catalogMatch,
    freeText: catalogMatch ? "" : ex.name,
    loadType: catalogMatch?.load_type ?? (ex.prescribed_load_kg != null ? "barbell" : "reps"),
    count: ex.sets ?? 1,
    target: {
      sets: ex.sets ?? null,
      reps: ex.reps ?? null,
      loadKg: ex.prescribed_load_kg ?? null,
      rpeCap: ex.rpe_cap ?? null,
      note: ex.load_note ?? null,
    },
    confirmed: false,
  };
}
