// src/perflab/prescription/todayRevision.ts
//
// P1: what the athlete is shown is an immutable, revisioned prescription
// (GET /v1/planning/today → `revision`). Every surface that shows today's ACTIONABLE
// session — Overview, Planning, the Log Workout pre-fill — reads it from that one
// endpoint, so they show the same revision and identical content. Pure (type imports
// only) so the rules stay testable without a network.
import type { ExercisePrescription, PrescriptionRevisionRead, TodaySessionResponse } from "@/types";

/** Reasons a revision REPLACED an earlier one, in the athlete's words. A first issue (or a
 * re-issue of pre-revision content) replaced nothing the athlete saw, so it says nothing. */
const REPLACEMENT_NOTICE: Record<string, string> = {
  issued_no_longer_safe:
    "Updated: the session you were shown no longer passes a safety check, so it was replaced.",
  safety_outcome_changed: "Updated: a safety restriction changed, so today's session was replaced.",
  safety_check_rerun:
    "Updated: a safety check that couldn't run earlier has now run, so today's session was re-issued.",
  athlete_recheck: "Re-checked: the earlier restriction has cleared.",
};

/** The notice for a revision that replaced an earlier one, else null. */
export function revisionNotice(revision: PrescriptionRevisionRead | null | undefined): string | null {
  if (!revision || revision.revision_no <= 1) return null;
  return REPLACEMENT_NOTICE[revision.reason] ?? null;
}

/** "Re-check" only means something while a safety restriction is in force: it can only
 * LIFT a restriction that has cleared. An unrestricted session never offers it. */
export function canRecheck(revision: PrescriptionRevisionRead | null | undefined): boolean {
  const kind = revision?.safety_kind;
  return kind != null && kind !== "none";
}

/** Where the Log Workout pre-fill comes from: today's planned session and the exact
 * revision shown for it — session id, revision id and exercises from ONE response, so the
 * log can never link one session while pre-filling another's content. */
export interface TodayPrefill {
  plannedSessionId: number;
  revisionId: number | null;
  exercises: ExercisePrescription[];
}

export function prefillFromToday(today: TodaySessionResponse | null | undefined): TodayPrefill | null {
  if (!today?.session || !today.prescription) return null;
  return {
    plannedSessionId: today.session.id,
    revisionId: today.revision?.id ?? null,
    exercises: today.prescription.exercises ?? [],
  };
}
