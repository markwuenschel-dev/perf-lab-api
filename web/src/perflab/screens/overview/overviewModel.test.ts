// src/perflab/screens/overview/overviewModel.test.ts
//
// THE BEHAVIOURAL HALF OF THE OVERVIEW HONESTY ORACLE (map #182, #189d).
//
// The static guard proves no fixture MODULE is reachable. It cannot prove that a
// value which happens to be fabricated never reaches the screen — a fallback
// plumbed through a parameter, the store, or a literal would pass it untouched.
// This file tests the other property: across the full product of resource states,
// no authenticated selector yields a value it was not given.
//
// Neither test substitutes for the other, which is why both are required.
import { describe, expect, it } from "vitest";
import type { AuthedResource } from "../../resource";
import type {
  OverviewMetrics,
  ReadinessScore,
  StateHistorySnapshotRead,
  WellnessSampleOut,
} from "@/types";
// Importing the fixtures is FINE here — a test is not an authenticated root. It is
// in fact the point: the oracle needs the real sample-athlete numbers so it can
// assert none of them appear in any authenticated output.
import { buildCheckin, DAYS } from "../../sim";
import {
  habitSection,
  loadSection,
  morningSection,
  primaryActionSection,
  readinessSection,
  trendSection,
  TREND_LABEL,
  twinSnapshotSection,
  PLAYABLE_SESSION_AVAILABLE,
  type MetricState,
} from "./overviewModel";

// ---- Builders -------------------------------------------------------------------

const guest = <T,>(): AuthedResource<T> => ({ status: "guest" });
const loading = <T,>(): AuthedResource<T> => ({ status: "loading" });
const failed = <T,>(): AuthedResource<T> => ({ status: "error", error: { message: "Network down" } });
const ok = <T,>(data: T): AuthedResource<T> => ({ status: "success", data, refresh: { status: "idle" } });

/**
 * A state-history row that deliberately OMITS `snapshot_id` and every confidence
 * field (#187: the annotation is in scope, its consequences are not). If any
 * selector starts depending on them, these objects stop satisfying it and the
 * suite goes red — which is the proof that correcting the type was hygiene rather
 * than covert feature expansion.
 */
function snapshot(over: Partial<StateHistorySnapshotRead> = {}): StateHistorySnapshotRead {
  return {
    timestamp: "2026-07-20T06:00:00Z",
    capacity_x: { aerobic: 301, max_strength: 122 },
    fatigue_f: { cns: 20, muscular: 30, metabolic: 25, structural: 15, tendon: 10, grip: 5 },
    tissue_t: { knee: 22, lumbar: 11 },
    c_met_aerobic: 301,
    c_nm_force: 2962, // the real mirror: 21 × 122 + 400
    ...over,
  } as unknown as StateHistorySnapshotRead;
}

function wellness(over: Partial<WellnessSampleOut> = {}): WellnessSampleOut {
  return {
    id: 1,
    user_id: 1,
    date: "2026-07-20",
    source: "manual",
    hrv_ms: 71,
    sleep_hours: 8.1,
    resting_hr: 47,
    soreness: 2,
    created_at: "2026-07-20T06:00:00Z",
    ...over,
  } as unknown as WellnessSampleOut;
}

function overviewMetrics(over: Partial<OverviewMetrics> = {}): OverviewMetrics {
  return {
    training_load: { acwr: 1.08, acute: 300, chronic: 280, status: "optimal", sweet_spot_low: 0.8, sweet_spot_high: 1.3 },
    adherence: { pct: 82, streak_days: 5, window_days: 28 },
    ...over,
  } as unknown as OverviewMetrics;
}

const readinessScore = (over: Partial<ReadinessScore> = {}): ReadinessScore =>
  ({ score: 71, wellness_delta: 0, components: [], ...over }) as unknown as ReadinessScore;

/** Every non-success arm of the canonical union, for state-product sweeps. */
const NON_SUCCESS = [
  ["guest", guest],
  ["loading", loading],
  ["error", failed],
] as const;

const isValue = <T,>(m: MetricState<T>) => m.kind === "value";

// ---- Readiness (PDR-0005, #189c) --------------------------------------------------

describe("readinessSection", () => {
  it("exposes the backend score, rounded, and never recomputes it", () => {
    const view = readinessSection(ok(readinessScore({ score: 71.4 })));
    expect(view.score).toEqual({ kind: "value", value: 71 });
  });

  it.each(NON_SUCCESS)("yields no number when the resource is %s", (_label, build) => {
    const view = readinessSection(build<ReadinessScore>());
    expect(isValue(view.score)).toBe(false);
    expect(JSON.stringify(view)).not.toMatch(/\d+(\.\d+)?/);
  });

  it("is empty, not unavailable, when the athlete simply has no modeled state", () => {
    const view = readinessSection(ok(readinessScore({ score: null })));
    expect(view.score).toEqual({ kind: "empty", reason: "no_state_yet" });
  });

  it("suppresses the confidence band when there is no score to describe", () => {
    const view = readinessSection(ok(readinessScore({ score: null, confidence: { band: "low" } } as Partial<ReadinessScore>)));
    expect(isValue(view.confidenceBand)).toBe(false);
  });

  it("NONINTERFERENCE: no signal other than the backend score can produce one (#189c)", () => {
    // Three check-in drafts that provably yield three DIFFERENT client-side
    // readiness numbers. They exist to establish that a local computation over
    // wellness is discriminating — i.e. that if one were reachable, it would show.
    const drafts = [
      { hrv: 20, sleepH: 2, sleepQ: 1, rhr: 95, soreness: "high" as const, mood: 1, stress: 5, done: true },
      { hrv: 64, sleepH: 7.5, sleepQ: 4, rhr: 52, soreness: "mild" as const, mood: 4, stress: 2, done: true },
      { hrv: 200, sleepH: 14, sleepQ: 5, rhr: 30, soreness: "none" as const, mood: 5, stress: 1, done: true },
    ];
    const derived = drafts.map((d) => buildCheckin(d).readiness);
    expect(new Set(derived).size).toBe(drafts.length);

    // The property under test is that NOTHING on the payload except `score` can
    // yield a readiness value. So vary every other field the payload carries —
    // including wellness components with large contributions, which is the most
    // plausible thing a future fallback would reach for — and require the output
    // to be identical while `score` stays null.
    const decoys: Partial<ReadinessScore>[] = drafts.map((d, i) => ({
      score: null,
      wellness_delta: derived[i],
      note: `derived ${derived[i]}`,
      components: [
        { signal: "hrv", value: d.hrv, baseline: 62, contribution: (d.hrv - 62) / 100 },
        { signal: "sleep_hours", value: d.sleepH, baseline: 7, contribution: (d.sleepH - 7) / 10 },
      ],
    })) as Partial<ReadinessScore>[];

    const outputs = decoys.map((over) => JSON.stringify(readinessSection(ok(readinessScore(over)))));
    expect(new Set(outputs).size).toBe(1);
    // And the single shared output carries no number at all.
    expect(outputs[0]).not.toMatch(/\d/);

    // The failure arm is likewise invariant and numberless.
    expect(JSON.stringify(readinessSection(failed<ReadinessScore>()))).not.toMatch(/\d/);

    // NOTE ON WHAT THIS CANNOT COVER: a fallback reaching a check-in value through
    // a MODULE-LEVEL import rather than through the payload would survive this
    // test. That route is closed by overviewBoundary.test.ts, which walks
    // overviewModel.ts's transitive value-imports and fails if sim.ts or a fixture
    // store is reachable. The two tests are complementary by design.
  });

  it("carries no trend or delta field (#186 — the trend is not readiness)", () => {
    const view = readinessSection(ok(readinessScore()));
    expect(Object.keys(view).sort()).toEqual(["confidenceBand", "score"]);
  });
});

// ---- Trend (#186) -------------------------------------------------------------------

describe("trendSection", () => {
  it("is labelled fatigue-derived, never readiness", () => {
    expect(trendSection(ok([])).label).toBe(TREND_LABEL);
    expect(TREND_LABEL.toLowerCase()).not.toContain("readiness");
  });

  it("is empty with zero rows — never a sim series", () => {
    const view = trendSection(ok([]));
    expect(view.series).toEqual({ kind: "empty", reason: "no_data" });
    expect(isValue(view.delta)).toBe(false);
  });

  it("is insufficient, not confident, with a single row", () => {
    const view = trendSection(ok([snapshot()]));
    expect(view.series).toEqual({ kind: "empty", reason: "insufficient_history" });
  });

  it("derives the series and delta from the rows it was given, oldest to newest", () => {
    const rows = [snapshot({ fatigue_f: { cns: 60, muscular: 60, metabolic: 60, structural: 60, tendon: 60, grip: 60 } } as Partial<StateHistorySnapshotRead>), snapshot()];
    const view = trendSection(ok(rows));
    expect(view.series.kind).toBe("value");
    if (view.series.kind === "value") {
      expect(view.series.value).toHaveLength(2);
      expect(view.series.value[0]).toBe(40); // 100 - 60
      expect(view.delta).toEqual({ kind: "value", value: view.series.value[1] - view.series.value[0] });
    }
  });

  it.each(NON_SUCCESS)("yields no series when the resource is %s", (_label, build) => {
    const view = trendSection(build<StateHistorySnapshotRead[]>());
    expect(isValue(view.series)).toBe(false);
    expect(isValue(view.delta)).toBe(false);
  });
});

// ---- Morning wellness ------------------------------------------------------------------

/** Readiness carrying the engine's own per-signal resolution for `date`. */
function resolvedReadiness(
  date: string,
  components: Array<{ signal: string; value: number }>,
): AuthedResource<ReadinessScore> {
  return ok({
    score: 71,
    components: components.map((c) => ({ ...c, baseline: c.value, contribution: 0 })),
    wellness_sample: wellness({ date }),
  } as unknown as ReadinessScore);
}

const noReadiness = <T,>() => guest<T>();

describe("morningSection", () => {
  it("renders every metric empty when the athlete has logged nothing", () => {
    const view = morningSection(ok([]), noReadiness<ReadinessScore>());
    expect(view.metrics).toHaveLength(4);
    view.metrics.forEach((m) => expect(m.value).toEqual({ kind: "empty", reason: "no_data" }));
    expect(view.lastLoggedDate).toEqual({ kind: "empty", reason: "no_data" });
  });

  it("resolves each field independently — a partial row is partly empty, not borrowed", () => {
    const view = morningSection(
      ok([wellness({ hrv_ms: 71, resting_hr: null })]),
      noReadiness<ReadinessScore>(),
    );
    const byKey = Object.fromEntries(view.metrics.map((m) => [m.key, m.value]));
    expect(byKey.hrv).toEqual({ kind: "value", value: "71 ms" });
    expect(byKey.resting_hr).toEqual({ kind: "empty", reason: "not_recorded" });
  });

  it("reports the sample's own date and makes no claim about 'today' (#191)", () => {
    const view = morningSection(ok([wellness({ date: "2026-07-18" })]), noReadiness<ReadinessScore>());
    expect(view.lastLoggedDate).toEqual({ kind: "value", value: "2026-07-18" });
  });

  it.each(NON_SUCCESS)("yields no metric values when the resource is %s", (_label, build) => {
    const view = morningSection(build<WellnessSampleOut[]>(), noReadiness<ReadinessScore>());
    view.metrics.forEach((m) => expect(isValue(m.value)).toBe(false));
  });

  // ---- Multi-source days -------------------------------------------------------
  // `WellnessSample` is keyed (user, date, source). Reading one row dropped whichever
  // source landed first; these are the regressions that proved it.

  it("keeps a signal only the OTHER source reported — the athlete's soreness is not dropped", () => {
    // Device synced after the check-in, so it sorts first and carries no soreness.
    const device = wellness({ id: 2, source: "oura", soreness: null, created_at: "2026-07-20T07:00:00Z" });
    const manual = wellness({ id: 1, source: "manual", soreness: 6, hrv_ms: null, created_at: "2026-07-20T06:00:00Z" });
    const view = morningSection(ok([device, manual]), noReadiness<ReadinessScore>());
    const byKey = Object.fromEntries(view.metrics.map((m) => [m.key, m.value]));
    expect(byKey.soreness).toEqual({ kind: "value", value: "Moderate" });
    expect(byKey.hrv).toEqual({ kind: "value", value: "71 ms" });
  });

  it("shows the value the engine used when two sources report the same signal", () => {
    const device = wellness({ id: 2, source: "oura", hrv_ms: 71, created_at: "2026-07-20T07:00:00Z" });
    const manual = wellness({ id: 1, source: "manual", hrv_ms: 58, created_at: "2026-07-20T06:00:00Z" });
    // The engine believed the device for HRV; the tile must not disagree with the ring.
    const view = morningSection(
      ok([manual, device]),
      resolvedReadiness("2026-07-20", [{ signal: "hrv_ms", value: 71 }]),
    );
    const byKey = Object.fromEntries(view.metrics.map((m) => [m.key, m.value]));
    expect(byKey.hrv).toEqual({ kind: "value", value: "71 ms" });
  });

  it("ignores the engine's resolution when it describes a different day", () => {
    const view = morningSection(
      ok([wellness({ date: "2026-07-20", hrv_ms: 58 })]),
      resolvedReadiness("2026-07-19", [{ signal: "hrv_ms", value: 71 }]),
    );
    const byKey = Object.fromEntries(view.metrics.map((m) => [m.key, m.value]));
    expect(byKey.hrv).toEqual({ kind: "value", value: "58 ms" });
  });

  it("folds only the latest date, never borrowing yesterday's reading", () => {
    const today = wellness({ id: 2, date: "2026-07-20", soreness: null });
    const yesterday = wellness({ id: 1, date: "2026-07-19", soreness: 8 });
    const view = morningSection(ok([today, yesterday]), noReadiness<ReadinessScore>());
    const byKey = Object.fromEntries(view.metrics.map((m) => [m.key, m.value]));
    expect(byKey.soreness).toEqual({ kind: "empty", reason: "not_recorded" });
    expect(view.lastLoggedDate).toEqual({ kind: "value", value: "2026-07-20" });
  });
});

// ---- Twin snapshot (#187) -----------------------------------------------------------------

describe("twinSnapshotSection", () => {
  it("is empty with no recorded state — never the sample athlete's capacities", () => {
    const view = twinSnapshotSection(ok([]));
    expect(isValue(view.aerobic)).toBe(false);
    expect(isValue(view.strength)).toBe(false);
    expect(isValue(view.meanFatigue)).toBe(false);
    expect(isValue(view.peakTissue)).toBe(false);
  });

  it("reads the newest row (the endpoint returns oldest to newest)", () => {
    const older = snapshot({ capacity_x: { aerobic: 100, max_strength: 100 }, c_met_aerobic: 100, c_nm_force: 100 } as Partial<StateHistorySnapshotRead>);
    const view = twinSnapshotSection(ok([older, snapshot()]));
    expect(view.aerobic).toEqual({ kind: "value", value: 301 });
  });

  it("behaves identically when snapshot_id and confidence fields are absent (#187 guard)", () => {
    const bare = snapshot();
    const enriched = snapshot({
      snapshot_id: 4242,
      capacity_confidence_status: { aerobic: "provisional" },
      confidence_presentation_policy_version: "v1",
    } as Partial<StateHistorySnapshotRead>);
    expect(JSON.stringify(twinSnapshotSection(ok([bare])))).toBe(JSON.stringify(twinSnapshotSection(ok([enriched]))));
  });

  it.each(NON_SUCCESS)("yields no capacities when the resource is %s", (_label, build) => {
    const view = twinSnapshotSection(build<StateHistorySnapshotRead[]>());
    expect(isValue(view.aerobic)).toBe(false);
    expect(isValue(view.peakTissue)).toBe(false);
  });
});

// ---- Load + habit (ADR-0046) ----------------------------------------------------------------

describe("loadSection / habitSection", () => {
  it("falls back to the backend's own 'insufficient' word, not an invented judgement", () => {
    expect(loadSection(failed<OverviewMetrics>()).status).toBe("insufficient");
  });

  it("leaves adherence EMPTY rather than 0 when nothing was scheduled", () => {
    const view = habitSection(ok(overviewMetrics({ adherence: { pct: null, streak_days: 0, window_days: 28 } } as Partial<OverviewMetrics>)));
    expect(view.pct).toEqual({ kind: "empty", reason: "no_data" });
    // The distinction ADR-0046 exists for: a 0 here would render a 0%-wide bar,
    // which is a claim about adherence, not an absence of one.
    expect(view.pct).not.toEqual({ kind: "value", value: 0 });
  });

  it.each(NON_SUCCESS)("yields no acwr or adherence when the resource is %s", (_label, build) => {
    expect(isValue(loadSection(build<OverviewMetrics>()).acwr)).toBe(false);
    expect(isValue(habitSection(build<OverviewMetrics>()).pct)).toBe(false);
  });
});

// ---- Session entry (L3 / #188) ----------------------------------------------------------------

describe("primaryActionSection", () => {
  it("is a named constant, and it is closed", () => {
    expect(PLAYABLE_SESSION_AVAILABLE).toBe(false);
    expect(primaryActionSection().canStartSession).toBe(false);
    expect(primaryActionSection().unavailableReason).toBeTruthy();
  });
});

// ---- The whole-screen property ------------------------------------------------------------------

describe("no authenticated state produces fixture-backed athlete data", () => {
  // The acceptance test for B5 is not that Overview got smaller. It is this.
  const today = DAYS[DAYS.length - 1];
  const FIXTURE_NUMBERS = [
    Math.round(today.C.aerobic),
    Math.round(today.C.strength),
    today.readiness,
    64, // check-in HRV default (store initialState)
    7.5, // check-in sleep hours
    52, // check-in resting HR
  ];

  const everySectionOutput = (r: AuthedResource<never>): string =>
    JSON.stringify([
      readinessSection(r as AuthedResource<ReadinessScore>),
      trendSection(r as AuthedResource<StateHistorySnapshotRead[]>),
      morningSection(r as AuthedResource<WellnessSampleOut[]>, r as AuthedResource<ReadinessScore>),
      twinSnapshotSection(r as AuthedResource<StateHistorySnapshotRead[]>),
      loadSection(r as AuthedResource<OverviewMetrics>),
      habitSection(r as AuthedResource<OverviewMetrics>),
    ]);

  it.each(NON_SUCCESS)("contains no sample-athlete number in the %s state", (_label, build) => {
    const json = everySectionOutput(build<never>());
    FIXTURE_NUMBERS.forEach((n) => expect(json).not.toContain(String(n)));
  });

  it("contains no sample-athlete number in the successful-but-empty state", () => {
    const json = JSON.stringify([
      readinessSection(ok(readinessScore({ score: null }))),
      trendSection(ok([])),
      morningSection(ok([]), ok(readinessScore({ score: null }))),
      twinSnapshotSection(ok([])),
      habitSection(ok(overviewMetrics({ adherence: { pct: null, streak_days: 0, window_days: 28 } } as Partial<OverviewMetrics>))),
    ]);
    FIXTURE_NUMBERS.forEach((n) => expect(json).not.toContain(String(n)));
  });
});

// ---- INT-05 C4: no legacy-mirror fallback ---------------------------------------------

describe("legacy scalar mirrors are never read (INT-05 C4)", () => {
  it("reports strength as not recorded, never the mis-scaled c_nm_force", () => {
    const row = snapshot({ capacity_x: { aerobic: 301 }, c_nm_force: 1450 } as Partial<StateHistorySnapshotRead>);
    expect(twinSnapshotSection(ok([row])).strength).toEqual({ kind: "empty", reason: "not_recorded" });
  });
  it("reports mean fatigue as not recorded without the decomposed vector", () => {
    const row = snapshot({ fatigue_f: undefined, f_met_systemic: 40, f_nm_central: 40, f_nm_peripheral: 40, f_struct_damage: 40 } as unknown as Partial<StateHistorySnapshotRead>);
    expect(twinSnapshotSection(ok([row])).meanFatigue).toEqual({ kind: "empty", reason: "not_recorded" });
  });
  it("follows the vectors when the mirrors disagree", () => {
    // Vector mean = (20+30+25+15+10+5)/6 = 17.5 → 18; the mirrors claim 90 everywhere.
    const row = snapshot({ c_met_aerobic: 9, f_met_systemic: 90, f_nm_central: 90, f_nm_peripheral: 90, f_struct_damage: 90 } as Partial<StateHistorySnapshotRead>);
    const view = twinSnapshotSection(ok([row]));
    expect(view.meanFatigue).toEqual({ kind: "value", value: 18 });
    expect(view.aerobic).toEqual({ kind: "value", value: 301 });
  });
  it("drops a trend row without a fatigue vector instead of filling it from the scalars", () => {
    const bare = snapshot({ fatigue_f: undefined, f_met_systemic: 0 } as unknown as Partial<StateHistorySnapshotRead>);
    // 100 − round-free mean 17.5 = 82.5 → 83 for each full row.
    expect(trendSection(ok([snapshot(), bare, snapshot()])).series).toEqual({ kind: "value", value: [83, 83] });
    expect(trendSection(ok([snapshot(), bare])).series).toEqual({ kind: "empty", reason: "insufficient_history" });
  });
});
