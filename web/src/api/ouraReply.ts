// src/api/ouraReply.ts
//
// What an Oura OAuth reply means for the attempt this tab is holding (P6b, ADR-0075). The tab
// forgets an attempt's inputs only on an outcome it recognizes as terminal. Anything it cannot
// read (a malformed body, an unknown status code, a lost reply) is uncertain, and the inputs are
// kept so Retry can ask the server again. Shared by the client (which rejects a malformed 200)
// and the Settings hook (which classifies refusals).
import type { ApiError, OAuthAttemptResult } from "../types";

/** Every reason the server gives for a failed attempt (docs/API_GUIDE.md, /complete). */
export const OURA_FAILURE_REASONS = [
  "expired",
  "cancelled",
  "claim_stale",
  "exchange_rejected",
  "exchange_failed",
] as const;
export type OuraFailureReason = (typeof OURA_FAILURE_REASONS)[number];

export function isFailureReason(r: unknown): r is OuraFailureReason {
  return typeof r === "string" && (OURA_FAILURE_REASONS as readonly string[]).includes(r);
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isPositiveInt(v: unknown): boolean {
  return typeof v === "number" && Number.isInteger(v) && v >= 1;
}

/** An ISO-8601 date-time as the server writes it (naive UTC or with an offset), and a real one.
 *  The calendar is checked field by field: `Date.parse` quietly rolls February 30 into March. */
const ISO_DATETIME =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d{1,6})?(Z|[+-](\d{2}):(\d{2}))?$/;
function daysIn(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate(); // day 0 of the next month
}
function isTimestamp(v: unknown): boolean {
  if (typeof v !== "string") return false;
  const m = ISO_DATETIME.exec(v);
  if (!m) return false;
  const [y, mo, d, h, mi, s] = m.slice(1, 7).map(Number);
  const offH = m[9] === undefined ? 0 : Number(m[9]);
  const offM = m[10] === undefined ? 0 : Number(m[10]);
  return (
    mo >= 1 && mo <= 12 && d >= 1 && d <= daysIn(y, mo) &&
    h <= 23 && mi <= 59 && s <= 59 && offH <= 23 && offM <= 59
  );
}

function isAbsent(v: unknown): boolean {
  return v === null || v === undefined;
}

/** A /complete or /cancel 200 body, if it is one of the shapes the server sends; else null.
 *  A success must carry its receipt; a failure, a known reason. */
export function readAttemptResult(body: unknown): OAuthAttemptResult | null {
  if (!isRecord(body)) return null;
  if (body.status === "succeeded") {
    const linked = body.linked;
    if (!isTimestamp(body.completed_at) || !isAbsent(body.reason)) return null;
    if (!isRecord(linked) || !isPositiveInt(linked.connection_id) || !isPositiveInt(linked.generation))
      return null;
    return body as OAuthAttemptResult;
  }
  if (body.status === "failed") {
    // A failure has a known reason, when it happened (or nothing yet), and no receipt.
    if (!isFailureReason(body.reason) || !isAbsent(body.linked)) return null;
    if (!isAbsent(body.completed_at) && !isTimestamp(body.completed_at)) return null;
    return body as OAuthAttemptResult;
  }
  if (body.status === "in_progress")
    return isAbsent(body.reason) && isAbsent(body.completed_at) && isAbsent(body.linked)
      ? (body as OAuthAttemptResult)
      : null;
  return null;
}

/** What one /complete or /cancel reply (a result, or the error it threw) says about the attempt. */
export type OuraReplyOutcome =
  | { kind: "succeeded" }
  | { kind: "failed"; reason: OuraFailureReason }
  /** 400 invalid_state / verifier_mismatch: these inputs can never finish this attempt. */
  | { kind: "refused" }
  | { kind: "signIn" }
  | { kind: "wrongAccount" }
  /** In progress, 503 outcome_unknown, a lost or unreadable reply, an unknown status code. */
  | { kind: "uncertain" };

const REFUSED_400 = new Set(["invalid_state", "verifier_mismatch"]);

export function outcomeOf(result: OAuthAttemptResult | null, err: unknown): OuraReplyOutcome {
  if (result) {
    const r = readAttemptResult(result);
    if (r?.status === "succeeded") return { kind: "succeeded" };
    if (r?.status === "failed" && isFailureReason(r.reason)) return { kind: "failed", reason: r.reason };
    return { kind: "uncertain" };
  }
  const status = (err as ApiError | undefined)?.status;
  const raw = ((err as ApiError | undefined)?.details as { detail?: unknown } | undefined)?.detail;
  const detail = isRecord(raw) ? raw : null;
  if (status === 409 && detail?.status === "failed" && isFailureReason(detail.reason))
    return { kind: "failed", reason: detail.reason };
  if (status === 400 && detail?.status === "invalid" && typeof detail.reason === "string" &&
      REFUSED_400.has(detail.reason))
    return { kind: "refused" };
  if (status === 401) return { kind: "signIn" };
  if (status === 403) return { kind: "wrongAccount" };
  return { kind: "uncertain" };
}
