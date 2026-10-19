// What the web may offer for a planned session, given the status a reconciling read showed.
//
// P2: the server persists `missed` for a past session nothing was logged for, but only when a
// reconciling read runs (GET /planning/sessions, /today, /week-review). Writers do not
// reconcile, so a stale `pending` past session and a `missed` one are different to the
// server: feedback is refused on the first and accepted on the second; a date-only move is
// accepted on the first and refused on the second. Every action here therefore keys off the
// status the screen was given — never off the date alone.
import type { PlannedSessionUpdateRequest, SessionStatus } from "@/types";
import { athleteTodayIso } from "./athleteTimezone";

export type Outcome = "completed" | "modified" | "skipped";

export const OUTCOMES: [Outcome, string][] = [
  ["completed", "As prescribed"],
  ["modified", "Changed it"],
  ["skipped", "Skipped"],
];

/**
 * The outcomes the feedback form offers. Mirrors the server's coherence rule
 * (session_feedback_service `_COHERENT_OUTCOMES`), so the form never offers an answer the
 * server refuses with a 409. A missed session had nothing recorded: training that did happen
 * is told by logging it, not here. With no status known, everything is offered, as before.
 */
export function outcomesFor(status: SessionStatus | null): [Outcome, string][] {
  if (status === "completed") return OUTCOMES.filter(([k]) => k !== "skipped");
  if (status === "skipped" || status === "missed") return OUTCOMES.filter(([k]) => k === "skipped");
  return OUTCOMES;
}

/** Feedback describes an outcome: completed, skipped, or (P2) missed. A past session that
 *  still reads `pending` has no outcome yet, whatever its date says. */
export function canGiveFeedback(status: SessionStatus): boolean {
  return status === "completed" || status === "skipped" || status === "missed";
}

/** A session that has not happened can be moved. A missed one too — by reopening it. */
export function isMovable(status: SessionStatus): boolean {
  return status === "pending" || status === "rescheduled" || status === "missed";
}

/**
 * The earliest day a missed session may be reopened onto. The server allows a reopen only on
 * the athlete's "today" or later (`check_patch`), which it counts in the athlete's timezone
 * (B1) — UTC while that is unknown. This browser's calendar can differ (a trip, or no zone
 * stored yet: at 00:30Z New York still reads the previous day), so the floor is the later of
 * the two dates.
 */
export function reopenFloorIso(
  localTodayIso: string,
  now: Date = new Date(),
  athleteZone: string | null = null,
): string {
  const athleteToday = athleteTodayIso(athleteZone, now);
  return athleteToday > localTodayIso ? athleteToday : localTodayIso;
}

/**
 * The PATCH a move sends. `missed` is inferred from the date, so the server refuses a
 * date-only move of a missed session; moving it reopens it in the same change. The week grid
 * only offers a missed session destinations on or after `reopenFloorIso`, which reopening
 * requires.
 */
export function moveRequest(status: SessionStatus | undefined, iso: string): PlannedSessionUpdateRequest {
  return status === "missed" ? { status: "pending", scheduled_date: iso } : { scheduled_date: iso };
}
