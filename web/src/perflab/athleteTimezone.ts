/**
 * The athlete's calendar on the web (B1). The backend owns the athlete's timezone
 * (`ProfileRead.timezone`, an IANA name; null = unknown, which the backend reads as UTC) and
 * resolves every "today" from it. The web uses the same zone wherever it sends or matches a
 * calendar day, so the two never disagree about which day it is.
 *
 * Instants stay UTC ISO strings (`toISOString()`); only calendar days go through here.
 */

/**
 * The ONE calendar identity every day-dependent resource is keyed on (B1). The server counts
 * "today" in the athlete's stored zone, so a response belongs to the calendar it was fetched
 * under; when the confirmed zone changes, that response is obsolete.
 *
 *   "pending"      the profile (and any zone capture) has not settled yet: don't fetch
 *   "unavailable"  the profile failed to load: the zone is not known to this client
 *   "unknown"      the server positively says no zone is stored (it counts UTC days)
 *   "zone:<IANA>"  the athlete's stored zone
 */
export type CalendarKey = "pending" | "unavailable" | "unknown" | `zone:${string}`;

export function calendarKeyOf(
  settled: boolean,
  profile: { timezone?: string | null } | null,
): CalendarKey {
  if (!settled) return "pending";
  if (profile === null) return "unavailable";
  return profile.timezone ? `zone:${profile.timezone}` : "unknown";
}

/** True when date-dependent writes may go ahead: the calendar is established (a known zone, or
 *  a profile that positively says unknown). Pending or unavailable → wait. */
export function calendarEstablished(key: CalendarKey | undefined): boolean {
  return key === "unknown" || (typeof key === "string" && key.startsWith("zone:"));
}

/**
 * The day the week grid labels as "today" — and judges a pending session against for "missed".
 * With a known zone it is the athlete's day, the server's. While the zone is unknown the server
 * counts the UTC day but keeps a day of grace before calling a session missed, so a session on
 * this browser's today is not missed yet even when UTC has already moved on: the grid uses the
 * EARLIER of this browser's day and the UTC day. (`through` and the move floors keep the
 * server's day: those are what it accepts.)
 */
export function gridTodayIso(
  zone: string | null | undefined,
  localTodayIso: string,
  now: Date = new Date(),
): string {
  const serverDay = athleteTodayIso(zone, now);
  if (zone) return serverDay;
  return localTodayIso < serverDay ? localTodayIso : serverDay;
}

/** Zones a runtime reports when it does not actually know where it is. */
const NOT_A_ZONE = new Set(["Etc/Unknown", "Factory"]);

/** The browser's IANA zone, or null when the runtime does not report a real one. */
export function browserTimezone(): string | null {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return typeof zone === "string" && zone.length > 0 && !NOT_A_ZONE.has(zone) ? zone : null;
  } catch {
    return null;
  }
}

/** Capture only while the athlete's zone is unknown; a known zone is never replaced. */
export function shouldCaptureTimezone(profile: { timezone?: string | null } | null): boolean {
  return profile !== null && (profile.timezone === null || profile.timezone === undefined);
}

/**
 * The athlete's calendar date ("YYYY-MM-DD") at the instant `now`, in `zone` — UTC when the
 * zone is unknown, as the backend does. An unusable zone also falls back to UTC.
 */
export function athleteTodayIso(zone: string | null | undefined, now: Date = new Date()): string {
  if (!zone) return now.toISOString().slice(0, 10);
  try {
    // en-CA formats as YYYY-MM-DD; formatToParts keeps it independent of locale quirks.
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(now);
    const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
    return `${get("year")}-${get("month")}-${get("day")}`;
  } catch {
    return now.toISOString().slice(0, 10);
  }
}

/** Offered when the runtime cannot list its zones (no `Intl.supportedValuesOf`). */
export const FALLBACK_TIMEZONES: readonly string[] = [
  "UTC",
  "America/Los_Angeles",
  "America/Denver",
  "America/Chicago",
  "America/New_York",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Africa/Johannesburg",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Brisbane",
  "Australia/Sydney",
  "Pacific/Auckland",
];

/**
 * Every IANA zone the runtime knows (for the Settings picker). Without
 * `Intl.supportedValuesOf` it is a short list of common zones plus this browser's; the
 * stored zone and UTC are always offered. The server validates whatever is chosen.
 */
export function knownTimezones(current: string | null | undefined): string[] {
  let zones: string[] = [];
  try {
    const supported = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf;
    zones = supported ? supported("timeZone") : [];
  } catch {
    zones = [];
  }
  if (zones.length === 0) {
    const own = browserTimezone();
    zones = [...FALLBACK_TIMEZONES, ...(own && !FALLBACK_TIMEZONES.includes(own) ? [own] : [])];
  }
  if (!zones.includes("UTC")) zones = ["UTC", ...zones];
  if (current && !zones.includes(current)) zones = [current, ...zones];
  return zones;
}
