// src/perflab/overlays/workoutLogBody.ts
//
// THE ONLY PLACE A `POST /v1/log-workout` (and `/v1/simulate-dose`) REQUEST BODY IS
// BUILT (#199).
//
// It lives in its own module, separate from LogWorkoutModal.tsx, for one structural
// reason: the modal legitimately value-imports the fixture module `../sim` for its
// preview chrome (COLORS, DOSE_NAMES, doseBarColor, PRESETS, projectLogDose), so a
// static reachability guard rooted at the modal could never be green. Rooted HERE the
// guard is meaningful and enforceable — see workoutLogBoundary.test.ts. Keep this
// module free of value imports from any fixture module; `import type` is fine, because
// a type-only edge is erased at build time and carries no data.
//
// The honesty contract this module exists to hold:
//
//   real athlete-entered value  -> submit it
//   not entered / unknown       -> OMIT the key entirely
//   guest / sample state        -> unreachable from here
//   zero or a neutral midpoint  -> NEVER used to mean unknown
//
// ...and its consequence for a field the backend makes REQUIRED. `duration_minutes`
// and `session_rpe` cannot be omitted (app/schemas/workouts.py), so "the athlete has
// not entered it" cannot be encoded in the body at all. The honest resolution is that
// THERE IS NO BODY: `buildWorkoutLog` returns `null`, and the caller must not submit.
// `missingRequiredReadings` names what is still needed so the UI can say so and keep
// Apply disabled. Previously these fields were seeded in the store (42 min / 9 km /
// RPE 7), so an untouched modal produced a complete, plausible, fictional body.
//
// The backend half (ADR-0049) makes `sleep_quality` / `life_stress_inverse` genuinely
// nullable: absent means "no check-in exists", is stored as SQL NULL, and contributes a
// LABELLED neutral (no dose penalty, zero confidence) instead of the old imputed 5.0.
// Omitting the key is therefore the correct wire encoding for "not reported" — it is
// not a way of dodging a required field. The regenerated contract confirms it:
// `sleep_quality?: number | null` in src/types.gen.ts, and neither field appears in
// WorkoutLog's `required` list.
import type { Modality, WorkoutLog } from "@/types";
import type { CheckinState } from "../sim";
import { deriveModality, groupsToSets, type SetGroup } from "./setBuilderLogic";

/**
 * The two wellness readings the workout log carries, on the BACKEND's 1–10 scale.
 * `null` means the athlete has not reported it — it is never a number standing in for
 * "unknown".
 */
export interface WorkoutWellness {
  /** 1 = worst sleep, 10 = best. null = not reported. */
  sleepQuality: number | null;
  /** 1 = very high life stress, 10 = none. null = not reported. */
  lifeStressInverse: number | null;
}

/** A wellness report in which nothing has been entered. The default, always. */
export const NO_WELLNESS_REPORTED: WorkoutWellness = {
  sleepQuality: null,
  lifeStressInverse: null,
};

/**
 * Map a check-in's 1–5 slider onto the backend's 1–10 scale, preserving "not reported".
 *
 * The endpoints anchor: 1 -> 1 and 5 -> 10, linearly, so `v -> 1 + (v - 1) * 9/4`.
 * (3/5 -> 5.5/10, the true middle of both scales.)
 *
 * This rescaling is also a bug fix. The previous code sent the raw 1–5 slider value
 * into a field the backend bounds to [1, 10] and scores against
 * `dose_human_factor_reference = 5.0`, so a 4/5 ("good") sleep arrived as 4/10 ("poor")
 * and earned a real dose penalty, and `mood` — capped at 5 — could never clear the
 * reference at all. It validated silently because [1,5] is inside [1,10]. The sibling
 * wellness path never had this bug: `wellnessSignals.metricValue` already rescales
 * (`sleepQ * 20` for the 0–100 field).
 */
export function fromFive(v: number | null): number | null {
  if (v === null) return null;
  return 1 + ((v - 1) * 9) / 4;
}

/**
 * The wellness a workout log may carry, derived from the morning check-in.
 *
 * Pure and total: a check-in in which nothing was entered yields
 * `NO_WELLNESS_REPORTED`, because every `CheckinState` self-report starts as `null`
 * (`store.initialState`). There is no seeded constant left for this to read.
 */
export function checkinToWorkoutWellness(c: CheckinState): WorkoutWellness {
  return {
    sleepQuality: fromFive(c.sleepQ),
    lifeStressInverse: fromFive(c.mood),
  };
}

/** The session modality the body will carry: derived from the sets when there are
 *  any (ADR-0045), else implied by the chosen session type. */
function resolveModality(logType: string, setGroups: SetGroup[]): Modality {
  const sets = groupsToSets(setGroups);
  return (
    (sets.length ? deriveModality(setGroups) : null) ??
    (logType === "strength" ? "Strength" : "Running")
  );
}

/** A reading the athlete must supply before this session can be logged. */
export type RequiredReading = "duration" | "effort" | "distance";

/**
 * Which required readings have not been entered. Empty means submittable.
 *
 * `duration` and `effort` are unconditional — the backend requires
 * `duration_minutes` and `session_rpe` on every log. `distance` is required only for
 * a session that would actually carry it: running-shaped with no per-set entry, the
 * one case `buildWorkoutLog` sends `distance_meters`. A strength session, or any
 * session logged per-set, never needs it.
 */
export function missingRequiredReadings(
  logType: string,
  rpe: number | null,
  durationMin: number | null,
  distanceKm: number | null,
  setGroups: SetGroup[] = [],
): readonly RequiredReading[] {
  const missing: RequiredReading[] = [];
  if (durationMin === null) missing.push("duration");
  if (rpe === null) missing.push("effort");
  const carriesDistance =
    groupsToSets(setGroups).length === 0 && resolveModality(logType, setGroups) === "Running";
  if (carriesDistance && distanceKm === null) missing.push("distance");
  return missing;
}

/** Build a backend WorkoutLog from the modal's form state, or `null` when the
 * athlete has not supplied every required reading.
 *
 * When per-set groups are present (ADR-0045) they are the record: `sets` is sent,
 * the session modality is derived from them (the backend derives it too), and the
 * running-shaped session distance is dropped so the backend rolls it up from sets.
 *
 * `plannedSessionId` is today's planned session the pre-fill came from. It is sent only
 * when the athlete recorded at least one of that session's prescribed exercises: the link
 * says "this log fulfils that plan", and an untouched recommendation says no such thing. */
export function buildWorkoutLog(
  logType: string,
  rpe: number | null,
  durationMin: number | null,
  distanceKm: number | null,
  wellness: WorkoutWellness,
  setGroups: SetGroup[] = [],
  plannedSessionId: number | null = null,
): WorkoutLog | null {
  if (missingRequiredReadings(logType, rpe, durationMin, distanceKm, setGroups).length) {
    return null;
  }
  const sets = groupsToSets(setGroups);
  const modality: Modality = resolveModality(logType, setGroups);
  const fulfilsPlan =
    plannedSessionId !== null && setGroups.some((g) => g.target !== undefined && g.confirmed);
  // We send only what the form captures; the backend fills server-side defaults
  // for omitted fields (is_benchmark, novelty, total_volume_load, …). `satisfies`
  // still type-checks the fields we DO set against the contract; the cast covers
  // the server-defaulted remainder.
  return {
    timestamp: new Date().toISOString(),
    modality,
    // Non-null by the guard above: every required reading was supplied.
    duration_minutes: durationMin as number,
    session_rpe: rpe as number,
    // Wellness is OMITTED, not defaulted, when the athlete has not reported it. The
    // key is absent from the JSON body entirely — the backend then records SQL NULL
    // and applies a labelled neutral rather than a fabricated midpoint (ADR-0049).
    ...(wellness.sleepQuality !== null ? { sleep_quality: wellness.sleepQuality } : {}),
    ...(wellness.lifeStressInverse !== null
      ? { life_stress_inverse: wellness.lifeStressInverse }
      : {}),
    ...(sets.length
      ? { sets }
      : modality === "Running"
        ? { distance_meters: Math.round((distanceKm as number) * 1000) }
        : {}),
    ...(fulfilsPlan ? { planned_session_id: plannedSessionId } : {}),
  } satisfies Partial<WorkoutLog> as WorkoutLog;
}
