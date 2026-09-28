// src/perflab/wearableSync.test.ts
//
// Wearable data reaches the check-in by syncing when the athlete shows up (the box sleeps
// overnight, so no cron can). These pin the staleness rule, the UTC reading of the
// backend's zone-less timestamps, and that a device-measured signal is never re-sent by hand.
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WellnessSampleOut } from "@/types";
import type { CheckinState } from "./sim";
import { buildWellnessSample, type SignalMode, type WellnessSignalKey } from "./wellnessSignals";

const getWearableConnection = vi.fn();
const syncOura = vi.fn();
vi.mock("@/api/perfLabClient", () => ({
  getWearableConnection: (...a: unknown[]) => getWearableConnection(...a),
  syncOura: (...a: unknown[]) => syncOura(...a),
}));

const ws = await import("./wearableSync");

const NOW = new Date("2026-09-28T12:00:00Z");

function row(over: Partial<WellnessSampleOut>): WellnessSampleOut {
  return {
    id: 1, user_id: 1, date: "2026-09-28", source: "oura", created_at: "2026-09-28T08:00:00",
    hrv_ms: null, resting_hr: null, sleep_hours: null, sleep_quality: null,
    soreness: null, mood: null, stress: null, raw: {},
    ...over,
  } as WellnessSampleOut;
}

describe("staleness", () => {
  it("reads a zone-less server timestamp as UTC, not local time", () => {
    expect(ws.parseServerUtc("2026-09-28T10:00:00").toISOString()).toBe("2026-09-28T10:00:00.000Z");
    expect(ws.parseServerUtc("2026-09-28T10:00:00Z").toISOString()).toBe("2026-09-28T10:00:00.000Z");
    expect(ws.parseServerUtc("2026-09-28T06:00:00-04:00").toISOString()).toBe("2026-09-28T10:00:00.000Z");
  });

  it("is stale after 6 hours, or when never synced", () => {
    expect(ws.isSyncStale(null, NOW)).toBe(true);
    expect(ws.isSyncStale("2026-09-28T07:00:00", NOW)).toBe(false); // 5 h ago
    expect(ws.isSyncStale("2026-09-28T05:59:00", NOW)).toBe(true); // just over 6 h
  });
});

describe("syncWearableIfStale", () => {
  beforeEach(() => {
    getWearableConnection.mockReset();
    syncOura.mockReset();
  });

  it("syncs a connected, stale wearable", async () => {
    getWearableConnection.mockResolvedValue({ connected: true, connection: { last_sync_at: "2026-09-27T20:00:00" } });
    syncOura.mockResolvedValue({ rows_written: 2 });
    expect(await ws.syncWearableIfStale("t", NOW)).toEqual({ rows_written: 2 });
  });

  it("does not sync when fresh or not connected", async () => {
    getWearableConnection.mockResolvedValue({ connected: true, connection: { last_sync_at: "2026-09-28T11:00:00" } });
    expect(await ws.syncWearableIfStale("t", NOW)).toBeNull();
    getWearableConnection.mockResolvedValue({ connected: false, connection: null });
    expect(await ws.syncWearableIfStale("t", NOW)).toBeNull();
    expect(syncOura).not.toHaveBeenCalled();
  });

  it("never throws: a failed sync must not block the check-in", async () => {
    getWearableConnection.mockResolvedValue({ connected: true, connection: { last_sync_at: null } });
    syncOura.mockRejectedValue(new Error("Oura sync failed"));
    expect(await ws.syncWearableIfStale("t", NOW)).toBeNull();
  });
});

describe("today's device readings", () => {
  it("takes the latest device row for the day, never a manual row or another day", () => {
    const readings = ws.deviceReadingsForDay([
      row({ source: "manual", hrv_ms: 40 }),
      row({ date: "2026-09-27", hrv_ms: 70 }),
      row({ created_at: "2026-09-28T06:00:00", hrv_ms: 55 }),
      row({ created_at: "2026-09-28T09:00:00", hrv_ms: 62, resting_hr: 48, sleep_hours: 7.4, sleep_quality: 88 }),
    ], "2026-09-28");
    expect(readings).toEqual({
      source: "oura",
      values: { hrv_ms: 62, resting_hr: 48, sleep_hours: 7.4, sleep_quality: 88 },
    });
    expect([...ws.deviceCoveredSignals(readings)].sort()).toEqual(["hrv", "rhr", "sleep"]);
  });

  it("covers only what the device actually reported", () => {
    const readings = ws.deviceReadingsForDay([row({ hrv_ms: 62 })], "2026-09-28");
    expect([...ws.deviceCoveredSignals(readings)]).toEqual(["hrv"]);
    expect(ws.deviceReadingsForDay([row({})], "2026-09-28")).toBeNull();
  });

  it("reads the OAuth result off the return URL", () => {
    expect(ws.ouraRedirectResult("?oura=connected")).toBe("connected");
    expect(ws.ouraRedirectResult("?oura=error")).toBe("error");
    expect(ws.ouraRedirectResult("?oura=nonsense")).toBeNull();
    expect(ws.ouraRedirectResult("")).toBeNull();
  });
});

describe("the manual check-in never competes with the device", () => {
  const ci: CheckinState = {
    hrv: 50, sleepH: 6, sleepQ: 3, rhr: 60, soreness: "mild", mood: 4, stress: 2, done: false,
  };
  const modes = Object.fromEntries(
    ["sleep", "hrv", "rhr", "soreness", "mood", "stress"].map((k) => [k, "provided"]),
  ) as Record<WellnessSignalKey, SignalMode>;

  it("omits every field of a device-covered signal and keeps the subjective ones", () => {
    const body = buildWellnessSample(ci, modes, new Set(), new Set<WellnessSignalKey>(["sleep", "hrv", "rhr"]));
    expect(body).not.toHaveProperty("hrv_ms");
    expect(body).not.toHaveProperty("resting_hr");
    expect(body).not.toHaveProperty("sleep_hours");
    expect(body).not.toHaveProperty("sleep_quality");
    expect(body).toMatchObject({ source: "manual", soreness: 3, mood: 8, stress: 4 });
  });

  it("still sends everything by hand when no device covered it", () => {
    const body = buildWellnessSample(ci, modes, new Set());
    expect(body).toMatchObject({ hrv_ms: 50, resting_hr: 60, sleep_hours: 6, sleep_quality: 60 });
  });
});
