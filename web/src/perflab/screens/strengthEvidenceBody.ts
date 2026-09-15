// src/perflab/screens/strengthEvidenceBody.ts
//
// THE ONLY PLACE A STRENGTH REPORT IS BUILT — for POST /v1/benchmarks/strength-evidence
// (Assess, Settings) and for the `strength` list of POST /v1/onboard.
//
// An athlete reports a strength observation for a canonical lift in one of three ways: a
// tested 1-rep max, a set they did (load × reps, with effort if they know it), or an
// estimate. The client states WHAT HAPPENED and nothing more. It never sends value semantics,
// evidence type, a prescription flag, or a computed e1RM — the server derives all of those,
// using the same qualification logic workout extraction uses, so the entry route cannot
// change what the evidence is allowed to do.
//
// The honesty contract, as in workoutLogBody.ts:
//
//   real athlete-entered value  -> send it
//   not entered / unknown       -> OMIT the key
//   zero, NaN, or a guess       -> never used to mean unknown
//
// Weights are typed in the athlete's selected unit and converted to kilograms HERE, at the
// request boundary; the API only ever speaks kilograms. A performance date is optional on its
// own: without one the observation is still recorded, but the server will not use it to size
// a load. Onboarding asks for it on a tested max or a set (`requireDate`). A chosen calendar
// day is sent as local midnight of that day, converted to an instant, so "today" is never in
// the future.
import { lbsToKg } from "@/lib/units";
import type { OnboardStrengthReport, StrengthEvidenceCreate } from "@/types";

export type StrengthMethod = "tested_max" | "rep_set" | "estimate";

/** The unit weights are typed in. Requests always carry kilograms. */
export type WeightUnit = "kg" | "lb";

/** The form exactly as typed, in the selected unit. Empty string means "not entered". */
export interface StrengthForm {
  method: StrengthMethod;
  /** The tested max or the estimate. */
  weight: string;
  /** The set's load. */
  load: string;
  reps: string;
  /** Optional effort for the set, 1–10. */
  rpe: string;
  /** `<input type="date">` value, "YYYY-MM-DD", or "" when unknown. */
  performedOn: string;
}

export type StrengthReportResult =
  | { ok: true; report: OnboardStrengthReport }
  | { ok: false; error: string };

export type StrengthBodyResult =
  | { ok: true; body: StrengthEvidenceCreate }
  | { ok: false; error: string };

export const EMPTY_STRENGTH_FORM: StrengthForm = {
  method: "tested_max",
  weight: "",
  load: "",
  reps: "",
  rpe: "",
  performedOn: "",
};

/** Nothing entered for this lift. A selected method alone is not a report. */
export function isBlankStrengthForm(form: StrengthForm): boolean {
  return [form.weight, form.load, form.reps, form.rpe, form.performedOn].every(
    (v) => v.trim() === "",
  );
}

/** A positive, finite weight in `unit`, as kilograms — or null when it is not one. */
function positiveKilograms(raw: string, unit: WeightUnit): number | null {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return null;
  return unit === "lb" ? lbsToKg(n) : n;
}

/** Local midnight of a "YYYY-MM-DD" day, or null when the string is not a real date. */
function localMidnight(day: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  // Reject rollovers like 2026-02-31, which Date silently turns into March.
  return d.getMonth() === Number(m[2]) - 1 && d.getDate() === Number(m[3]) ? d : null;
}

/** One lift's report, without a collection mode (the route that receives it supplies that). */
export function strengthReport(
  benchmarkCode: string,
  form: StrengthForm,
  options: { now?: Date; requireDate?: boolean; unit?: WeightUnit } = {},
): StrengthReportResult {
  const now = options.now ?? new Date();
  const unit = options.unit ?? "kg";
  let performedAt: string | undefined;
  if (form.performedOn === "") {
    if (options.requireDate && form.method !== "estimate") {
      return { ok: false, error: "Add the date you did it." };
    }
  } else {
    const day = localMidnight(form.performedOn);
    if (!day) return { ok: false, error: "Enter a real date." };
    if (day.getTime() > now.getTime()) return { ok: false, error: "That date is in the future." };
    performedAt = day.toISOString();
  }

  const base = {
    benchmark_code: benchmarkCode,
    method: form.method,
    ...(performedAt !== undefined ? { performed_at: performedAt } : {}),
  };

  if (form.method === "rep_set") {
    const load = positiveKilograms(form.load, unit);
    if (load === null) return { ok: false, error: `Enter the load you lifted, in ${unit}.` };
    const reps = Number(form.reps);
    if (form.reps.trim() === "" || !Number.isInteger(reps) || reps < 1) {
      return { ok: false, error: "Enter how many reps you did." };
    }
    let rpe: number | undefined;
    if (form.rpe.trim() !== "") {
      const r = Number(form.rpe);
      if (!Number.isFinite(r) || r < 1 || r > 10) {
        return { ok: false, error: "Effort (RPE) is a number from 1 to 10." };
      }
      rpe = r;
    }
    return {
      ok: true,
      report: { ...base, load_kg: load, reps, ...(rpe !== undefined ? { rpe } : {}) },
    };
  }

  const value = positiveKilograms(form.weight, unit);
  if (value === null) return { ok: false, error: `Enter the weight in ${unit}.` };
  return { ok: true, report: { ...base, value_kg: value } };
}

/** A report submitted on its own, from Assess or Settings. */
export function strengthEvidenceBody(
  benchmarkCode: string,
  mode: "onramp" | "retest",
  form: StrengthForm,
  options: { now?: Date; unit?: WeightUnit } = {},
): StrengthBodyResult {
  const built = strengthReport(benchmarkCode, form, options);
  if (!built.ok) return built;
  return {
    ok: true,
    body: { ...built.report, collection_mode: mode === "onramp" ? "onboarding_onramp" : "retest" },
  };
}
