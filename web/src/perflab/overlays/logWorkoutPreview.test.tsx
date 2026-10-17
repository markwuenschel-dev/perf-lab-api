// @vitest-environment jsdom
//
// src/perflab/overlays/logWorkoutPreview.test.tsx
//
// P8: the D(t) preview a SIGNED-IN athlete sees is the real /v1/simulate-dose answer
// for the draft on screen, or an honest state (missing, computing, unavailable). Never
// the sample bars, never an earlier draft's answer, never the sample-projected S(t)
// shift. The guest demo keeps its illustrative bars and S(t) panel.
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkoutLog } from "@/types";

/** null = guest. */
let authToken: string | null = "test-token";

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({
    token: authToken,
    user: authToken ? { email: "athlete@example.com" } : null,
    profile: null,
    email: authToken ? "athlete@example.com" : null,
    isGuest: authToken === null,
  }),
}));

interface Pending {
  body: WorkoutLog;
  resolve: (six: number[] | null) => void;
  reject: () => void;
}
/** Every /v1/simulate-dose call, answered by the test. */
let calls: Pending[] = [];

vi.mock("@/api/perfLabClient", () => ({
  logWorkout: () => Promise.resolve({ state_disposition: "applied" }),
  simulateDose: (body: WorkoutLog) =>
    new Promise((resolve, reject) => {
      calls.push({
        body,
        resolve: (six) =>
          resolve({
            dose_six: six && {
              volume: six[0], intensity: six[1], density: six[2],
              impact: six[3], skill: six[4], metabolic: six[5],
            },
          }),
        reject: () => reject(new Error("boom")),
      });
    }),
  getNextSession: () => Promise.resolve({ exercises: [] }),
  getTodayPlannedSession: () => Promise.resolve({ session: null, prescription: null }),
  listExercises: () => Promise.resolve([]),
}));

interface Draft {
  rpe: number | null;
  durationMin: number | null;
  distanceKm: number | null;
}
let draft: Draft = { rpe: 7, durationMin: 40, distanceKm: 8 };

vi.mock("../store", () => ({
  usePerfLab: () => ({
    state: {
      logOpen: true,
      logType: "tempo",
      ...draft,
      paceSec: null,
      checkin: { hrv: null, sleepH: null, sleepQ: null, rhr: null, soreness: null, mood: null, stress: null, done: false },
      settings: { goal: "Hypertrophy" },
    },
    actions: {
      closeLog: vi.fn(), openAuth: vi.fn(), cacheTwinState: vi.fn(), applyLog: vi.fn(),
      refreshWorkouts: vi.fn(), setRpe: vi.fn(), setLogType: vi.fn(), setDur: vi.fn(),
      setDist: vi.fn(), setPaceSec: vi.fn(),
    },
  }),
}));

/** The guest demo's sample vector, recognisable in the DOM ("9.9"). */
const SAMPLE = [9.9, 9.9, 9.9, 9.9, 9.9, 9.9];
vi.mock("../sim", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../sim")>();
  return {
    ...actual,
    projectLogDose: () => ({
      scaled: SAMPLE,
      readyAfter: 61, fatAfter: 31, capDelta: "+1", cap: "Aerobic", zone: "Z3", readyColor: "#fff",
    }),
  };
});

const { LogWorkoutModal } = await import("./LogWorkoutModal");

const X = [1.1, 1.2, 1.3, 1.4, 1.5, 1.6];
const Y = [2.1, 2.2, 2.3, 2.4, 2.5, 2.6];

/** The values the D(t) bars show, or null when no bars are rendered. */
function bars(): string[] | null {
  const el = screen.queryByTestId("dose-bars");
  if (!el) return null;
  return [...el.textContent!.matchAll(/\d+\.\d/g)].map((m) => m[0]);
}
const shown = (six: number[]) => six.map((v) => v.toFixed(1));

async function flushDebounce() {
  await act(async () => {
    vi.advanceTimersByTime(350);
  });
}
async function answer(i: number, six: number[] | null) {
  await act(async () => {
    calls[i].resolve(six);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-16T10:00:00Z"));
  authToken = "test-token";
  calls = [];
  draft = { rpe: 7, durationMin: 40, distanceKm: 8 };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("signed in: the D(t) preview is real or honestly absent", () => {
  it("missing readings: names them, sends nothing, shows no bars", async () => {
    draft = { rpe: null, durationMin: null, distanceKm: 8 };
    render(<LogWorkoutModal />);
    await flushDebounce();
    expect(calls).toHaveLength(0);
    expect(bars()).toBeNull();
    expect(screen.getByText("Enter duration and perceived effort to preview the dose.")).toBeTruthy();
  });

  it("pending, then the real answer; never the sample bars", async () => {
    render(<LogWorkoutModal />);
    expect(screen.getByText("Computing dose…")).toBeTruthy();
    expect(bars()).toBeNull();
    await flushDebounce();
    expect(calls).toHaveLength(1);
    expect(bars()).toBeNull();
    await answer(0, X);
    expect(bars()).toEqual(shown(X));
  });

  it("a failed request is unavailable, not sample bars", async () => {
    render(<LogWorkoutModal />);
    await flushDebounce();
    await act(async () => calls[0].reject());
    expect(bars()).toBeNull();
    expect(screen.getByText("Dose preview unavailable — you can still log.")).toBeTruthy();
  });

  it("a response with no dose is unavailable too", async () => {
    render(<LogWorkoutModal />);
    await flushDebounce();
    await answer(0, null);
    expect(bars()).toBeNull();
    expect(screen.getByText("Dose preview unavailable — you can still log.")).toBeTruthy();
  });

  it("an edit drops the previous draft's bars on the same render, before the debounce", async () => {
    const { rerender } = render(<LogWorkoutModal />);
    await flushDebounce();
    await answer(0, X);
    expect(bars()).toEqual(shown(X));

    draft = { ...draft, rpe: 8 };
    rerender(<LogWorkoutModal />);
    expect(bars()).toBeNull();
    expect(screen.getByText("Computing dose…")).toBeTruthy();
    expect(calls).toHaveLength(1); // the debounce has not fired yet
  });

  it("a slow answer for the old draft is dropped after an edit", async () => {
    const { rerender } = render(<LogWorkoutModal />);
    await flushDebounce();
    expect(calls).toHaveLength(1);

    draft = { ...draft, durationMin: 55 };
    rerender(<LogWorkoutModal />);
    await answer(0, X); // the answer for the 40-minute draft arrives late
    expect(bars()).toBeNull();

    await flushDebounce();
    expect(calls).toHaveLength(2);
    expect(calls[1].body.duration_minutes).toBe(55);
    await answer(1, Y);
    expect(bars()).toEqual(shown(Y));
  });

  it("success, then a failure for the next draft: no old bars", async () => {
    const { rerender } = render(<LogWorkoutModal />);
    await flushDebounce();
    await answer(0, X);
    draft = { ...draft, rpe: 9 };
    rerender(<LogWorkoutModal />);
    await flushDebounce();
    await act(async () => calls[1].reject());
    expect(bars()).toBeNull();
    expect(screen.getByText("Dose preview unavailable — you can still log.")).toBeTruthy();
  });

  it("an unchanged draft keeps its preview when it re-renders after the clock moves", async () => {
    const { rerender } = render(<LogWorkoutModal />);
    await flushDebounce();
    await answer(0, X);
    expect(bars()).toEqual(shown(X));

    vi.setSystemTime(new Date("2026-10-16T10:05:00Z"));
    rerender(<LogWorkoutModal />);
    await flushDebounce();
    expect(bars()).toEqual(shown(X));
    expect(calls).toHaveLength(1); // no new request for the same draft
  });

  it("shows no S(t) shift panel", async () => {
    render(<LogWorkoutModal />);
    await flushDebounce();
    await answer(0, X);
    expect(screen.queryByText("Resulting S(t) shift")).toBeNull();
  });
});

describe("guest demo keeps its illustration", () => {
  it("sample bars and the S(t) panel before any answer", async () => {
    authToken = null;
    render(<LogWorkoutModal />);
    expect(bars()).toEqual(shown(SAMPLE));
    expect(screen.getByText("Resulting S(t) shift")).toBeTruthy();
    await flushDebounce();
    await answer(0, X);
    expect(bars()).toEqual(shown(X));
  });
});
