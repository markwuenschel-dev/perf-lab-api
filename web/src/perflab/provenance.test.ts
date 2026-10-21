// INT-15 2C: the provenance contract below the screens.
//
//   - a 409 `display_state_unavailable` body becomes a CODED ApiError with the fixed message
//   - that coded refusal discards a payload already on screen (an older snapshot must never
//     stand in for the current one); any other failed refresh keeps the payload, as before
//   - the Overview selectors report it as `state_unavailable`, never as empty
//   - recovered rows are flagged per plotted point and on the twin snapshot
//
// Expected values are written out by hand, not derived from the helpers under test.
import { afterEach, describe, expect, it, vi } from "vitest";
import type { StateHistorySnapshotRead } from "@/types";
import { isRecovered, mixedWindowNote, recoveredCount } from "./provenance";
import { reduceResource, toResourceError, type AuthedResource } from "./resource";
import { trendSection, twinSnapshotSection } from "./screens/overview/overviewModel";

const MESSAGE =
  "Your training-state history can't be shown right now: a stored snapshot can't be read. It needs a repair on our side; nothing you change here will fix it.";

const fatigue = (v: number) => ({ cns: v, muscular: v, metabolic: v, structural: v, tendon: v, grip: v });
const snap = (id: string, f: number, recovered: boolean): StateHistorySnapshotRead =>
  ({
    snapshot_id: id,
    timestamp: "2026-10-01T08:00:00Z",
    fatigue_f: fatigue(f),
    capacity_x: { aerobic: 400, glycolytic: 50, max_strength: 60, power: 50, work_capacity: 60 },
    state_source: recovered ? "legacy_recovery" : "canonical",
    degraded: recovered,
    degradation_reason: recovered ? "null_engine_state_legacy_row" : null,
    codec_version: "v2",
  }) as unknown as StateHistorySnapshotRead;

describe("the client codes the display refusal", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  async function historyFails(status: number, body: unknown) {
    vi.stubEnv("VITE_API_BASE_URL", "http://api.test");
    vi.resetModules();
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })),
      ),
    );
    const client = await import("@/api/perfLabClient");
    return client.getStateHistory("tok", 60).then(
      () => {
        throw new Error("expected a rejection");
      },
      (e: unknown) => e as { message: string; code?: string; status?: number },
    );
  }

  it("turns the 409 body into a coded error with the fixed message", async () => {
    const err = await historyFails(409, {
      error: "display_state_unavailable",
      history_available: false,
      resolution_available_in_product: false,
    });
    expect(err.code).toBe("display_state_unavailable");
    expect(err.status).toBe(409);
    expect(err.message).toBe(MESSAGE);
  });

  it("leaves other 409s uncoded", async () => {
    const err = await historyFails(409, { error: "canonical_state_invalid" });
    expect(err.code).toBeUndefined();
  });
});

describe("the coded refusal discards a payload on screen", () => {
  const onScreen: AuthedResource<number[]> = { status: "success", data: [1, 2], refresh: { status: "loading" } };

  it("a coded refusal drops the old payload", () => {
    const next = reduceResource(onScreen, {
      type: "request-failed",
      error: toResourceError({ message: MESSAGE, code: "display_state_unavailable" }),
    });
    expect(next).toEqual({ status: "error", error: { message: MESSAGE, code: "display_state_unavailable" } });
  });

  it("an ordinary failed refresh still keeps the payload (unchanged behaviour)", () => {
    const next = reduceResource(onScreen, { type: "request-failed", error: toResourceError(new Error("boom")) });
    expect(next.status).toBe("success");
  });
});

describe("Overview selectors", () => {
  const unavailable: AuthedResource<StateHistorySnapshotRead[]> = {
    status: "error",
    error: { message: MESSAGE, code: "display_state_unavailable" },
  };

  it("report state_unavailable, distinct from empty and from a load failure", () => {
    expect(trendSection(unavailable).series).toEqual({ kind: "unavailable", reason: "state_unavailable" });
    expect(twinSnapshotSection(unavailable).aerobic).toEqual({ kind: "unavailable", reason: "state_unavailable" });
    const failed: AuthedResource<StateHistorySnapshotRead[]> = { status: "error", error: { message: "x" } };
    expect(trendSection(failed).series).toEqual({ kind: "unavailable", reason: "load_failed" });
    const empty: AuthedResource<StateHistorySnapshotRead[]> = { status: "success", data: [], refresh: { status: "idle" } };
    expect(trendSection(empty).series).toEqual({ kind: "empty", reason: "no_data" });
  });

  it("flag each plotted trend point and the newest twin snapshot", () => {
    const mixed: AuthedResource<StateHistorySnapshotRead[]> = {
      status: "success",
      data: [snap("a", 10, false), snap("b", 20, true), snap("c", 30, false)],
      refresh: { status: "idle" },
    };
    expect(trendSection(mixed).recoveredPoints).toEqual([false, true, false]);
    expect(twinSnapshotSection(mixed).recovered).toBe(false);

    const recoveredHead: AuthedResource<StateHistorySnapshotRead[]> = {
      status: "success",
      data: [snap("a", 10, false), snap("b", 20, true)],
      refresh: { status: "idle" },
    };
    expect(twinSnapshotSection(recoveredHead).recovered).toBe(true);
  });

  it("canonical rows carry no recovered flag", () => {
    const canonical: AuthedResource<StateHistorySnapshotRead[]> = {
      status: "success",
      data: [snap("a", 10, false), snap("b", 20, false)],
      refresh: { status: "idle" },
    };
    expect(trendSection(canonical).recoveredPoints).toEqual([false, false]);
    expect(twinSnapshotSection(canonical).recovered).toBe(false);
  });
});

describe("helpers", () => {
  it("read provenance from either field, and treat missing fields as canonical", () => {
    expect(isRecovered({ state_source: "legacy_recovery" })).toBe(true);
    expect(isRecovered({ degraded: true })).toBe(true);
    expect(isRecovered({ state_source: "canonical", degraded: false })).toBe(false);
    expect(isRecovered({})).toBe(false);
    expect(recoveredCount([{ degraded: true }, {}, { state_source: "legacy_recovery" }])).toBe(2);
  });

  it("say whether points are marked, and say nothing for a canonical window", () => {
    expect(mixedWindowNote(0, 5, { marked: true })).toBeNull();
    expect(mixedWindowNote(2, 5, { marked: true })).toBe(
      "2 of 5 points are recovered estimates (marked); confidence not measured for those.",
    );
    expect(mixedWindowNote(2, 5, { marked: false })).toBe(
      "2 of 5 points are recovered estimates; confidence not measured for those.",
    );
    expect(mixedWindowNote(3, 3, { marked: false })).toBe("All 3 points are recovered estimates; confidence not measured.");
  });
});
