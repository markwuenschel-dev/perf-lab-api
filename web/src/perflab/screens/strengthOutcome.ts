// src/perflab/screens/strengthOutcome.ts
//
// THE ONE PLACE the athlete is told what happened to a strength report that yielded no estimate
// (P4-3) — Assess, Settings and onboarding all use it. A reported set whose effort is unknown,
// or whose RPE and RIR disagree, is kept as a report (`report_only`) and the server derives no
// 1RM from it. The copy states that and nothing more: it never promises the set will count, or
// what a corrected one would be allowed to do.
import { kgToLbs } from "@/lib/units";
import type { StrengthEvidenceCreate, StrengthOutcome, UnderivedStrengthReportRead } from "@/types";
import type { WeightUnit } from "./strengthEvidenceBody";

export const EFFORT_CONFLICT_MESSAGE =
  "RPE and RIR disagree. This set is recorded, but no 1RM estimate was derived. Correct the effort information to characterize it.";

/** A report's load in the athlete's unit, as typed elsewhere on screen ("100 kg", "225 lb"). */
function displayLoad(kg: number, unit: WeightUnit): string {
  return unit === "lb" ? `${kgToLbs(kg).toFixed(0)} lb` : `${Number(kg.toFixed(1))} kg`;
}

/** What to tell the athlete about one underived report. */
export function underivedReportMessage(report: UnderivedStrengthReportRead, unit: WeightUnit): string {
  if (report.reason === "effort_conflict") return EFFORT_CONFLICT_MESSAGE;
  return `${displayLoad(report.load_kg, unit)} × ${report.reps} recorded; 1RM estimate unavailable because effort is unknown.`;
}

/** The notice for a submission's outcome, or null when it was recorded as evidence. */
export function outcomeNotice(outcome: StrengthOutcome, unit: WeightUnit): string | null {
  if (outcome.report == null) return null;
  return underivedReportMessage(outcome.report, unit);
}

/** The acknowledgement of one save, from the outcome the server returned: what was recorded,
 *  and the value when there is one. Settings shows it the moment the save returns, whatever a
 *  later read of the reports does. */
export function outcomeAcknowledgement(
  outcome: StrengthOutcome,
  unit: WeightUnit,
  method: StrengthEvidenceCreate["method"],
): string {
  const earlier = outcome.outcome === "already_recorded" ? "Already recorded earlier. " : "";
  if (outcome.report != null) return `${earlier}${underivedReportMessage(outcome.report, unit)}`;
  if (outcome.observation != null && outcome.value_available) {
    // The submitted method says what the value is (the observation read carries no semantics):
    // a tested max is a measured 1RM; a set's e1RM or a reported estimate is an estimate.
    const what = method === "tested_max" ? "1RM" : "Estimated 1RM";
    return `${earlier || "Saved. "}${what} ${displayLoad(outcome.observation.raw_value, unit)} recorded.`;
  }
  return earlier ? earlier.trim() : "Saved.";
}

/** Shown while a save got no answer: it may or may not have been recorded. */
export const UNCONFIRMED_SAVE_MESSAGE =
  "Your last save wasn't confirmed, so it may already be recorded. Retry sends the same save again and won't record it twice. Discard only forgets it here: if it was recorded, it stays recorded.";
