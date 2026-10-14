// src/perflab/wearableSync.ts
//
// Wearable data reaches the check-in by syncing when the athlete shows up, not by a
// nightly job: the production box sleeps overnight, so a cron cannot be relied on. The app
// syncs on load and when the check-in opens, only if the last sync is stale. A failed sync
// never blocks anything; the check-in simply asks for the signal by hand.
import { getWearableConnection, syncOura } from "@/api/perfLabClient";
import type { SyncResult, WellnessSampleOut } from "@/types";
import type { WellnessSignalKey } from "./wellnessSignals";

export const SYNC_STALE_AFTER_MS = 6 * 60 * 60 * 1000;

/** The backend stores naive UTC and serializes it without a zone. `new Date()` would read
 *  that as LOCAL time, so a zone-less timestamp is pinned to UTC here. */
export function parseServerUtc(ts: string): Date {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(ts) ? ts : `${ts}Z`);
}

export function isSyncStale(lastSyncAt: string | null | undefined, now: Date): boolean {
  if (!lastSyncAt) return true;
  return now.getTime() - parseServerUtc(lastSyncAt).getTime() > SYNC_STALE_AFTER_MS;
}

/** Sync the connected wearable when its data is stale. Resolves `null` when nothing was
 *  synced (not connected, fresh, or the sync failed); never throws. */
export async function syncWearableIfStale(token: string, now = new Date()): Promise<SyncResult | null> {
  try {
    const status = await getWearableConnection(token);
    if (!status.connected || !isSyncStale(status.connection?.last_sync_at, now)) return null;
    return await syncOura(token);
  } catch {
    return null;
  }
}

/** The day readiness scores against: the backend's UTC date, the same one the check-in posts. */
export function utcDay(now: Date): string {
  return now.toISOString().slice(0, 10);
}

/** The wellness fields a device measures, per check-in signal (wellness_source_authority's
 *  OBJECTIVE_SIGNALS). A device reading of these outranks a hand-entered one. */
export const DEVICE_FIELDS = {
  sleep: ["sleep_hours", "sleep_quality"],
  hrv: ["hrv_ms"],
  rhr: ["resting_hr"],
} as const satisfies Partial<Record<WellnessSignalKey, readonly (keyof WellnessSampleOut)[]>>;

export type DeviceField = (typeof DEVICE_FIELDS)[keyof typeof DEVICE_FIELDS][number];

export interface DeviceReadings {
  source: string;
  values: Partial<Record<DeviceField, number>>;
}

/** Today's device-measured values, from the most recently ingested device row for `day`. */
export function deviceReadingsForDay(rows: readonly WellnessSampleOut[], day: string): DeviceReadings | null {
  const device = rows
    .filter((r) => r.date === day && r.source !== "manual")
    .sort((a, b) => parseServerUtc(b.created_at).getTime() - parseServerUtc(a.created_at).getTime());
  for (const row of device) {
    const values: Partial<Record<DeviceField, number>> = {};
    for (const fields of Object.values(DEVICE_FIELDS)) {
      for (const f of fields) {
        const v = row[f];
        if (typeof v === "number") values[f] = v;
      }
    }
    if (Object.keys(values).length > 0) return { source: row.source, values };
  }
  return null;
}

/** A check-in signal the device already measured today: shown as the device's value and
 *  not asked again. Sleep counts as covered once the device reports its duration. */
export function deviceCoveredSignals(readings: DeviceReadings | null): Set<WellnessSignalKey> {
  const out = new Set<WellnessSignalKey>();
  if (!readings) return out;
  if (readings.values.sleep_hours !== undefined) out.add("sleep");
  if (readings.values.hrv_ms !== undefined) out.add("hrv");
  if (readings.values.resting_hr !== undefined) out.add("rhr");
  return out;
}

export function sourceLabel(source: string): string {
  return source === "oura" ? "Oura" : source.replace(/_/g, " ");
}

/** Where Oura's consent screen returns the browser (P6b, ADR-0075): an SPA route, so the
 *  completion call carries the athlete's session. The query holds Oura's `code` (or `error`)
 *  and the attempt's `state`. */
export const OURA_CALLBACK_PATH = "/settings/oura-callback";
/** sessionStorage key holding this tab's PKCE attempt (its verifier). The previous release
 *  clears exactly this key when it meets a callback it cannot finish. */
export const OURA_PKCE_STORAGE_KEY = "perflab.oura.pkce";
/** The retired API callback sends a consent started before the cutover here
 *  (`?oura=error&reason=interrupted`, crossing 1). */
export const OURA_UPDATE_INTERRUPTED_MESSAGE =
  "Oura sign-in was interrupted by an update. Connect Oura again.";

export function isOuraCallbackPath(pathname: string): boolean {
  return pathname.replace(/\/+$/, "") === OURA_CALLBACK_PATH;
}

/** The outcome the legacy redirect put in the URL (`/settings?oura=connected|error`). */
export function ouraRedirectResult(search: string): "connected" | "error" | null {
  const v = new URLSearchParams(search).get("oura");
  return v === "connected" || v === "error" ? v : null;
}

export type OuraReturn =
  | { kind: "connected" }
  | { kind: "error"; reason: string | null }
  | { kind: "callback"; state: string | null; code: string | null; error: string | null };

let capturedReturn: OuraReturn | null | undefined;
let returnDelivered = false;

function captureFromUrl(): OuraReturn | null {
  const params = new URLSearchParams(window.location.search);
  if (isOuraCallbackPath(window.location.pathname)) {
    return {
      kind: "callback",
      state: params.get("state"),
      code: params.get("code"),
      error: params.get("error"),
    };
  }
  const legacy = ouraRedirectResult(window.location.search);
  if (legacy === "connected") return { kind: "connected" };
  if (legacy === "error") return { kind: "error", reason: params.get("reason") };
  return null;
}

/** What this page load came back from Oura with, read from the URL the first time it is asked
 *  and never again: the URL is cleaned right after, and StrictMode replays effects, so a
 *  second read of the URL would find nothing. */
export function readOuraReturn(): OuraReturn | null {
  if (capturedReturn === undefined) capturedReturn = captureFromUrl();
  return capturedReturn;
}

/** `readOuraReturn` if nobody has taken it yet, without taking it (render-safe). */
export function peekOuraReturn(): OuraReturn | null {
  return returnDelivered ? null : readOuraReturn();
}

/** `readOuraReturn`, handed out once: the first caller gets it, every later one gets null. */
export function takeOuraReturn(): OuraReturn | null {
  const result = readOuraReturn();
  if (returnDelivered) return null;
  returnDelivered = true;
  return result;
}

/** Tests only: forget the captured return, as a fresh page load would. */
export function resetOuraReturnForTests(): void {
  capturedReturn = undefined;
  returnDelivered = false;
}

/** A phone automation can fail silently (phone locked, offline, automation off). After this
 *  long without a successful push, Settings says so rather than let the data go stale
 *  unnoticed. 36 h: a daily automation that missed one morning. */
export const PUSH_STALE_AFTER_MS = 36 * 60 * 60 * 1000;

export function isPushStale(lastUsedAt: string | null | undefined, now: Date): boolean {
  if (!lastUsedAt) return false; // never pushed: "not synced yet", not a warning
  return now.getTime() - parseServerUtc(lastUsedAt).getTime() > PUSH_STALE_AFTER_MS;
}
