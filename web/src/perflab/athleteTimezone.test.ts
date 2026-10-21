// src/perflab/athleteTimezone.test.ts
//
// B1: the web counts the athlete's day in the athlete's stored zone, as the backend does, and
// falls back to UTC (the backend's reading of an unknown zone).
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  FALLBACK_TIMEZONES,
  athleteTodayIso,
  browserTimezone,
  gridTodayIso,
  knownTimezones,
  shouldCaptureTimezone,
} from "./athleteTimezone";
import { reopenFloorIso } from "./sessionActions";
import { buildWellnessSample } from "./wellnessSignals";

describe("athleteTodayIso", () => {
  it("is still yesterday at UTC-8 after UTC midnight", () => {
    const now = new Date("2026-01-15T03:00:00Z");
    expect(athleteTodayIso("America/Los_Angeles", now)).toBe("2026-01-14");
    expect(athleteTodayIso(null, now)).toBe("2026-01-15");
  });

  it("is already tomorrow at UTC+10 before UTC midnight", () => {
    const now = new Date("2026-07-15T15:00:00Z");
    expect(athleteTodayIso("Australia/Brisbane", now)).toBe("2026-07-16");
    expect(athleteTodayIso(undefined, now)).toBe("2026-07-15");
  });

  it("follows DST in New York and London, not a fixed offset", () => {
    // Spring forward: 04:30Z on Mar 9 is 00:30 EDT (a fixed EST offset says Mar 8).
    expect(athleteTodayIso("America/New_York", new Date("2026-03-09T04:30:00Z"))).toBe("2026-03-09");
    // Fall back: 04:30Z on Nov 2 is 23:30 EST on Nov 1 (a fixed EDT offset says Nov 2).
    expect(athleteTodayIso("America/New_York", new Date("2026-11-02T04:30:00Z"))).toBe("2026-11-01");
    expect(athleteTodayIso("Europe/London", new Date("2026-07-01T23:30:00Z"))).toBe("2026-07-02");
    expect(athleteTodayIso("Europe/London", new Date("2026-12-01T23:30:00Z"))).toBe("2026-12-01");
  });

  it("falls back to UTC for a zone the runtime cannot use", () => {
    expect(athleteTodayIso("Mars/Olympus", new Date("2026-01-15T03:00:00Z"))).toBe("2026-01-15");
  });
});

describe("capture only while unknown", () => {
  it("captures for an unknown zone and never for a known one", () => {
    expect(shouldCaptureTimezone({ timezone: null })).toBe(true);
    expect(shouldCaptureTimezone({})).toBe(true);
    expect(shouldCaptureTimezone({ timezone: "UTC" })).toBe(false);
    expect(shouldCaptureTimezone({ timezone: "America/New_York" })).toBe(false);
    expect(shouldCaptureTimezone(null)).toBe(false);
  });

  afterEach(() => vi.restoreAllMocks());

  it("reads the browser's resolved IANA zone, or null", () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      timeZone: "Europe/London",
    } as Intl.ResolvedDateTimeFormatOptions);
    expect(browserTimezone()).toBe("Europe/London");
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      timeZone: "",
    } as Intl.ResolvedDateTimeFormatOptions);
    expect(browserTimezone()).toBeNull();
  });

  it("treats a runtime that does not know its zone as no zone", () => {
    for (const fake of ["Etc/Unknown", "Factory"]) {
      vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
        timeZone: fake,
      } as Intl.ResolvedDateTimeFormatOptions);
      expect(browserTimezone()).toBeNull();
    }
  });

  it("offers a fallback list when the runtime cannot list its zones", () => {
    const intl = Intl as unknown as { supportedValuesOf?: unknown };
    const original = intl.supportedValuesOf;
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      timeZone: "America/Halifax",
    } as Intl.ResolvedDateTimeFormatOptions);
    intl.supportedValuesOf = undefined;
    try {
      const zones = knownTimezones(null);
      expect(zones).toEqual(expect.arrayContaining([...FALLBACK_TIMEZONES, "America/Halifax"]));
      expect(zones.filter((z) => z === "UTC")).toHaveLength(1);
    } finally {
      intl.supportedValuesOf = original;
    }
  });

  it("offers UTC and the stored zone in the picker", () => {
    const zones = knownTimezones("Etc/Weird");
    expect(zones).toContain("UTC");
    expect(zones[0]).toBe("Etc/Weird");
  });
});

describe("gridTodayIso", () => {
  const nyEvening = new Date("2026-10-01T01:00:00Z"); // Sep 30 21:00 in New York
  it("keeps the earlier of the browser's day and UTC's while the zone is unknown", () => {
    expect(gridTodayIso(null, "2026-09-30", nyEvening)).toBe("2026-09-30");
    expect(gridTodayIso(undefined, "2026-10-01", new Date("2026-09-30T23:30:00Z"))).toBe("2026-09-30");
  });
  it("is the athlete's own day once the zone is known", () => {
    expect(gridTodayIso("America/New_York", "2026-10-01", nyEvening)).toBe("2026-09-30");
    expect(gridTodayIso("Australia/Brisbane", "2026-09-30", nyEvening)).toBe("2026-10-01");
  });
});

describe("callers that send or match a day", () => {
  it("floors a reopen at the athlete's today when it is later than this browser's", () => {
    // Brisbane is already Jul 16 while a New York browser still reads Jul 15.
    expect(reopenFloorIso("2026-07-15", new Date("2026-07-15T15:00:00Z"), "Australia/Brisbane")).toBe(
      "2026-07-16",
    );
    // Unknown zone: the UTC day, as before B1.
    expect(reopenFloorIso("2026-10-06", new Date("2026-10-07T00:30:00Z"))).toBe("2026-10-07");
    // An athlete in LA, browser in LA: their own day, not the UTC one.
    expect(reopenFloorIso("2026-01-14", new Date("2026-01-15T03:00:00Z"), "America/Los_Angeles")).toBe(
      "2026-01-14",
    );
  });

  it("posts the check-in on the day it is given", () => {
    const body = buildWellnessSample(
      { sleep: 7, hrv: 60, rhr: 50, soreness: "none", mood: 3, stress: 3 } as never,
      {} as never,
      new Set(),
      new Set(),
      athleteTodayIso("America/Los_Angeles", new Date("2026-01-15T03:00:00Z")),
    );
    expect(body.date).toBe("2026-01-14");
  });
});
