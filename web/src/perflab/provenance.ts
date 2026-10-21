// src/perflab/provenance.ts
//
// INT-15 2C: where a displayed training-state value came from, decided once.
//
// The backend's display surfaces (`/v1/state-history`, `/v1/dashboard/readiness`) now say,
// per snapshot, whether its values are the canonical stored state or a RECOVERY rebuilt from
// the older legacy scalar mirror because the canonical payload could not be read. A recovered
// value is real data with a caveat: it may be shown, never without the warning, and its
// confidence is not measured (a recovery carries only the weak prior, so any
// established / provisional / insufficient band on it would be invented).
//
// When a stored snapshot cannot be shown even as a recovery, the backend refuses the whole
// window with 409 `display_state_unavailable` instead of dropping the row: every consumer here
// reads the LAST row as the latest state, so a dropped head would present an older snapshot
// as current. That refusal is permanent until repaired, so it also discards a payload already
// on screen (see `reduceResource`).

/** The 409 body's `error` code for a display surface that cannot show the stored state. */
export const DISPLAY_STATE_UNAVAILABLE = "display_state_unavailable" as const;

/** The athlete-facing explanation for that refusal. Nothing they change here fixes it. */
export const STATE_UNAVAILABLE_MESSAGE =
  "Your training-state history can't be shown right now: a stored snapshot can't be read. It needs a repair on our side; nothing you change here will fix it.";

/** The caveat every recovered value carries wherever it is shown. */
export const RECOVERED_WARNING =
  "Recovered estimate: rebuilt from older stored values; confidence not measured.";

/** The provenance fields a snapshot carries. Optional so older payloads read as canonical. */
export interface SnapshotProvenance {
  state_source?: "canonical" | "legacy_recovery" | null;
  degraded?: boolean | null;
}

/** True when this snapshot's values were recovered from the legacy mirror. */
export function isRecovered(row: SnapshotProvenance | null | undefined): boolean {
  return row != null && (row.degraded === true || row.state_source === "legacy_recovery");
}

/** How many of `rows` are recoveries. */
export function recoveredCount(rows: readonly SnapshotProvenance[]): number {
  return rows.reduce((n, r) => (isRecovered(r) ? n + 1 : n), 0);
}

/** True for the coded "state cannot be shown" refusal, from any error shape. */
export function isStateUnavailable(error: unknown): boolean {
  return (error as { code?: unknown } | null)?.code === DISPLAY_STATE_UNAVAILABLE;
}

/** The note shown under a window that mixes canonical and recovered points, or null. */
export function mixedWindowNote(
  recovered: number,
  total: number,
  { marked }: { marked: boolean },
): string | null {
  if (recovered === 0) return null;
  if (recovered === total) return `All ${total} points are recovered estimates; confidence not measured.`;
  return `${recovered} of ${total} points are recovered estimates${marked ? " (marked)" : ""}; confidence not measured for those.`;
}
