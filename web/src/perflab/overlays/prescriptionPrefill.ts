// src/perflab/overlays/prescriptionPrefill.ts
//
// Turns the workout the app is currently recommending into Log Workout groups. Pure and
// fixture-free (type imports only), so the modal's fetch code stays thin and this stays
// testable without a network.
//
// Which workout is "the current recommendation" (P1): today's ISSUED revision from
// GET /v1/planning/today — the same revision Overview and Planning show — resolved in
// prescription/todayRevision.ts (prefillFromToday). Only with nothing planned today does the
// modal fall back to a /v1/next-session preview, and then nothing is linked.
import type { ExerciseCatalogOut, ExercisePrescription } from "@/types";
import type { SetGroup } from "./setBuilderLogic";

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
