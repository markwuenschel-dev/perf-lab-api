// @vitest-environment jsdom
//
// src/perflab/overlays/logWorkoutRequest.test.tsx
//
// THE REQUEST-BODY INTEGRATION HALF OF THE #199 ORACLE.
//
// The pure suite proves `buildWorkoutLog` is honest; the static guard proves no fixture
// module is reachable from it. Neither proves that the MODAL — the thing an athlete
// actually clicks — hands that honest body to the API client. This renders the real
// component with a real store state and captures the exact object passed to
// `logWorkout`, which is the last frontend artefact before the wire.
//
// FIELD-NAME MAP, asserted explicitly on both sides of the rename:
//     store `checkin.sleepQ` -> WorkoutLog.sleep_quality
//     store `checkin.mood`   -> WorkoutLog.life_stress_inverse
// The renamed field is the one a half-done fix would leave broken, so every case below
// asserts on `life_stress_inverse` by name, never by analogy with sleep.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CheckinState } from "../sim";
import type { ExercisePrescription, TodaySessionResponse, WorkoutLog } from "@/types";

const AUTH_TOKEN = "test-token";

/** Own-property check. `Object.hasOwn` needs the ES2022 lib; this repo targets ES2020. */
const hasKey = (o: Record<string, unknown>, k: string) =>
  Object.prototype.hasOwnProperty.call(o, k);

/** Every body handed to POST /v1/log-workout during a test. */
const logged: WorkoutLog[] = [];
/** Every body handed to the unauthenticated POST /v1/simulate-dose preview. */
const simulated: WorkoutLog[] = [];

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({
    token: AUTH_TOKEN,
    user: { email: "athlete@example.com" },
    profile: null,
    email: "athlete@example.com",
    isGuest: false,
  }),
}));

/** What the pre-fill reads: today's plan and the fresh prescription. Empty by default. */
/** GET /v1/planning/today. `null` = nothing planned today. */
let todayResponse: TodaySessionResponse | null = null;
let nextSessionRx: { exercises: ExercisePrescription[] } = { exercises: [] };
/** Every goal the modal asked GET /v1/next-session for. */
const nextSessionGoals: string[] = [];

/** What POST /v1/log-workout answers (P3a: it says whether the state was updated). */
let logResponse: Record<string, unknown> = { timestamp: "2026-07-30T00:00:00Z", state_disposition: "applied" };
const applyLog = vi.fn();
const refreshWorkouts = vi.fn();
/** The store's logOpen; flipped to simulate closing and reopening the (still mounted) modal. */
let storeLogOpen = true;

vi.mock("@/api/perfLabClient", () => ({
  logWorkout: (log: WorkoutLog) => {
    logged.push(log);
    return Promise.resolve(logResponse);
  },
  simulateDose: (log: WorkoutLog) => {
    simulated.push(log);
    return Promise.resolve({ dose_six: null });
  },
  getNextSession: (goal: string) => {
    nextSessionGoals.push(goal);
    return Promise.resolve(nextSessionRx);
  },
  getTodayPlannedSession: () =>
    Promise.resolve(todayResponse ?? ({ session: null, prescription: null } as TodaySessionResponse)),
  listExercises: () => Promise.resolve([]),
}));

const checkin = (over: Partial<CheckinState> = {}): CheckinState => ({
  hrv: null, sleepH: null, sleepQ: null, rhr: null, soreness: null, mood: null, stress: null, done: false,
  ...over,
});

let storeCheckin: CheckinState = checkin();

/** The four readings the athlete enters in the modal. `null` = not entered. */
interface Draft {
  rpe: number | null;
  durationMin: number | null;
  distanceKm: number | null;
  paceSec: number | null;
}
/** A draft in which every required reading HAS been supplied. */
const COMPLETE_DRAFT: Draft = { rpe: 7, durationMin: 42, distanceKm: 9, paceSec: 278 };
let storeDraft: Draft = { ...COMPLETE_DRAFT };
/** The athlete's training goal as the store holds it (settings.goal). */
let storeGoal = "Hypertrophy";

vi.mock("../store", () => ({
  usePerfLab: () => ({
    state: {
      logOpen: storeLogOpen,
      logType: "strength",
      ...storeDraft,
      checkin: storeCheckin,
      settings: { goal: storeGoal },
      sim: {},
    },
    actions: {
      closeLog: vi.fn(),
      openAuth: vi.fn(),
      cacheTwinState: vi.fn(),
      applyLog,
      refreshWorkouts,
      setRpe: vi.fn(),
      setLogType: vi.fn(),
      setDur: vi.fn(),
      setDist: vi.fn(),
      setPaceSec: vi.fn(),
    },
  }),
}));

// The dose-projection helper reads a much larger slice of state than this test builds;
// the preview panel is not what is under test, so it is stubbed to a fixed vector.
vi.mock("../sim", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../sim")>();
  return {
    ...actual,
    projectLogDose: () => ({
      scaled: [0, 0, 0, 0, 0, 0],
      readyAfter: 60, fatAfter: 30, capDelta: "+1", cap: "Aerobic", zone: "Z3", readyColor: "#fff",
    }),
  };
});

const { LogWorkoutModal } = await import("./LogWorkoutModal");

// The REAL store's initial check-in — deliberately not the local `checkin()` helper.
// The mock above replaces the store for the component, so without this the whole
// integration test would be insulated from the actual seed and would stay green if
// `store.initialState` reintroduced one. This is the link that makes the frontend
// inversion (restore the seed) turn THIS file red.
const realStore = await vi.importActual<typeof import("../store")>("../store");
const REAL_INITIAL_CHECKIN = realStore.initialState().checkin;
// Same trick for the workout-log draft. The store mock above insulates this file from
// the real seed, so without reading it here a restored `durationMin: 42` would sail
// through. This is the link that makes reintroducing the fixture seed turn THIS file red.
const s0 = realStore.initialState();
const REAL_INITIAL_DRAFT: Draft = {
  rpe: s0.rpe,
  durationMin: s0.durationMin,
  distanceKm: s0.distanceKm,
  paceSec: s0.paceSec,
};

beforeEach(() => {
  logged.length = 0;
  simulated.length = 0;
  storeCheckin = checkin();
  storeDraft = { ...COMPLETE_DRAFT };
});
afterEach(cleanup);

/** Render, click "Apply to twin", and return the body handed to `logWorkout`. */
async function submit(ci: CheckinState): Promise<Record<string, unknown>> {
  storeCheckin = ci;
  render(<LogWorkoutModal />);
  fireEvent.click(screen.getByText(/Apply to twin/));
  await vi.waitFor(() => expect(logged.length).toBe(1));
  return logged[0] as unknown as Record<string, unknown>;
}

describe("what the log did to the training state (P3a)", () => {
  afterEach(() => {
    logResponse = { timestamp: "2026-07-30T00:00:00Z", state_disposition: "applied" };
    applyLog.mockClear();
    refreshWorkouts.mockClear();
    storeLogOpen = true;
  });

  it("asks the server to time a live log by its own clock", async () => {
    const b = await submit(REAL_INITIAL_CHECKIN);
    expect(b.timestamp_mode).toBe("server_now");
    expect(typeof b.timestamp).toBe("string"); // the device time still goes along, for audit
  });

  it("an applied log closes to the twin as before, and tells workout readers to re-read", async () => {
    await submit(REAL_INITIAL_CHECKIN);
    await vi.waitFor(() => expect(applyLog).toHaveBeenCalledTimes(1));
    expect(refreshWorkouts).toHaveBeenCalledTimes(1);
  });

  it("a record-only log stays open and says the training-state update was omitted", async () => {
    logResponse = {
      timestamp: "2026-07-30T00:00:00Z",
      state_disposition: "record_only",
      state_disposition_reason: "event_before_current_state",
    };
    await submit(REAL_INITIAL_CHECKIN);
    const status = await screen.findByRole("status");
    expect(status.textContent).toMatch(/Training-state update omitted: it happened before your model's latest update/);
    expect(status.textContent).toMatch(/strength evidence from it are kept/);
    expect(applyLog).not.toHaveBeenCalled();
    // The workout was saved: the screen behind the form must re-read it (review repro).
    expect(refreshWorkouts).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Done" })).toBeTruthy();
    expect(screen.queryByText(/Apply to twin/)).toBeNull();
  });

  it.each(["Done", "Cancel"])("closing with %s and reopening starts a fresh draft that can be submitted again (review repro)", async (closeWith) => {
    logResponse = {
      timestamp: "2026-07-30T00:00:00Z",
      state_disposition: "record_only",
      state_disposition_reason: "event_before_current_state",
    };
    storeCheckin = REAL_INITIAL_CHECKIN;
    const view = render(<LogWorkoutModal />);
    fireEvent.click(screen.getByText(/Apply to twin/));
    await screen.findByRole("status");
    fireEvent.click(screen.getByRole("button", { name: closeWith }));

    // The store closes the modal, then the athlete opens it again for the next session.
    storeLogOpen = false;
    view.rerender(<LogWorkoutModal />);
    storeLogOpen = true;
    view.rerender(<LogWorkoutModal />);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("button", { name: "Done" })).toBeNull();

    logResponse = { timestamp: "2026-07-30T00:00:00Z", state_disposition: "applied" };
    fireEvent.click(screen.getByText(/Apply to twin/));
    await vi.waitFor(() => expect(logged.length).toBe(2));
    await vi.waitFor(() => expect(applyLog).toHaveBeenCalledTimes(1));
  });
});

describe("the object handed to logWorkout (#199)", () => {
  it("NO CHECK-IN: omits sleep_quality AND life_stress_inverse entirely", async () => {
    const b = await submit(REAL_INITIAL_CHECKIN);
    expect(hasKey(b, "sleep_quality"), "sleep_quality must be absent").toBe(false);
    expect(hasKey(b, "life_stress_inverse"), "life_stress_inverse must be absent").toBe(false);
    // And absent on the wire, not merely undefined in the object.
    const wire = JSON.parse(JSON.stringify(b));
    expect(wire).not.toHaveProperty("sleep_quality");
    expect(wire).not.toHaveProperty("life_stress_inverse");
  });

  it("NO CHECK-IN: neither field is 5, 5.5, 7, or 0", async () => {
    const b = await submit(REAL_INITIAL_CHECKIN);
    for (const forbidden of [0, 5, 5.5, 7, 7.5]) {
      expect(b.sleep_quality).not.toBe(forbidden);
      expect(b.life_stress_inverse).not.toBe(forbidden);
    }
  });

  it("COMPLETED CHECK-IN: submits exactly the athlete's values on the 1-10 scale", async () => {
    // sleepQ 5/5 -> 10/10 ; mood 1/5 -> 1/10 (deliberately opposite ends, so a mapper
    // that transposed the two fields would fail rather than coincide).
    const b = await submit(checkin({ sleepQ: 5, mood: 1, done: true }));
    expect(b.sleep_quality).toBe(10);
    expect(b.life_stress_inverse).toBe(1);
  });

  it("NAMING SEAM: the store's `mood` lands in `life_stress_inverse`, not `sleep_quality`", async () => {
    const b = await submit(checkin({ mood: 5, done: true }));
    expect(b.life_stress_inverse, "frontend `mood` must map to backend `life_stress_inverse`").toBe(10);
    expect(hasKey(b, "sleep_quality"), "`mood` must not leak into sleep_quality").toBe(false);
  });

  it("NAMING SEAM: the store's `sleepQ` lands in `sleep_quality`, not `life_stress_inverse`", async () => {
    const b = await submit(checkin({ sleepQ: 5, done: true }));
    expect(b.sleep_quality).toBe(10);
    expect(hasKey(b, "life_stress_inverse")).toBe(false);
  });

  it("PARTIAL: sleep reported, motivation not — one value, one omission", async () => {
    const b = await submit(checkin({ sleepQ: 3, done: true }));
    expect(b.sleep_quality).toBe(5.5);
    expect(hasKey(b, "life_stress_inverse")).toBe(false);
  });

  it("PARTIAL: motivation reported, sleep not", async () => {
    const b = await submit(checkin({ mood: 3, done: true }));
    expect(b.life_stress_inverse).toBe(5.5);
    expect(hasKey(b, "sleep_quality")).toBe(false);
  });

  it("the unauthenticated simulate-dose preview omits them too", async () => {
    // /v1/simulate-dose shares the WorkoutLog schema and must not be broken by the
    // change — nor may it be the surface that reintroduces a fabricated value.
    storeCheckin = REAL_INITIAL_CHECKIN;
    render(<LogWorkoutModal />);
    await vi.waitFor(() => expect(simulated.length).toBeGreaterThan(0));
    const b = simulated[0] as unknown as Record<string, unknown>;
    expect(hasKey(b, "sleep_quality")).toBe(false);
    expect(hasKey(b, "life_stress_inverse")).toBe(false);
  });
});

// ---------------------------------------------------------------------------------
// UNENTERED READINGS ARE NOT A WORKOUT
//
// The defect this pins: the log draft used to be SEEDED (rpe 7 / 42 min / 9 km), the
// inputs were uncontrolled `defaultValue`s, and Apply was disabled only while a request
// was in flight. A signed-in athlete could open the modal and click Apply without typing
// anything, and a complete, plausible, entirely fictional session went to the real
// backend and moved their twin.
//
// Note what does NOT catch this: every case above still passes with the defect present,
// because they mock a store whose draft is already fully populated. Honesty about an
// unentered value can only be tested by leaving it unentered.
// ---------------------------------------------------------------------------------

const applyBtn = () => screen.getByText(/Apply to twin/) as HTMLButtonElement;

describe("an untouched draft cannot be logged", () => {
  it("THE SEED IS GONE: the real store opens with every required reading unset", () => {
    // Reading the REAL store, not the mock — this is the seed-inversion link.
    expect(REAL_INITIAL_DRAFT.durationMin, "durationMin must open unentered").toBeNull();
    expect(REAL_INITIAL_DRAFT.rpe, "rpe must open unentered").toBeNull();
    expect(REAL_INITIAL_DRAFT.distanceKm, "distanceKm must open unentered").toBeNull();
    expect(REAL_INITIAL_DRAFT.paceSec, "paceSec must open unentered").toBeNull();
  });

  it("APPLY IS DISABLED and no request is made from the real initial draft", async () => {
    storeDraft = { ...REAL_INITIAL_DRAFT };
    render(<LogWorkoutModal />);
    expect(applyBtn().disabled, "Apply must be disabled while readings are unentered").toBe(true);
    fireEvent.click(applyBtn());
    // Give any errant async submit a turn of the loop to land.
    await new Promise((r) => setTimeout(r, 0));
    expect(logged.length, "no workout may be logged from an untouched modal").toBe(0);
  });

  it("nothing is SIMULATED from an untouched draft either", async () => {
    storeDraft = { ...REAL_INITIAL_DRAFT };
    render(<LogWorkoutModal />);
    await new Promise((r) => setTimeout(r, 400)); // past the 350ms preview debounce
    expect(simulated.length, "the preview must not simulate a fictional session").toBe(0);
  });

  it("says WHICH readings are missing, by name", () => {
    storeDraft = { ...REAL_INITIAL_DRAFT };
    render(<LogWorkoutModal />);
    expect(screen.getByText(/Enter duration and perceived effort to log this session\./)).
      toBeTruthy();
  });

  it("a partially entered draft is still refused", () => {
    storeDraft = { ...REAL_INITIAL_DRAFT, durationMin: 42 };
    render(<LogWorkoutModal />);
    expect(applyBtn().disabled, "duration alone is not enough").toBe(true);
    expect(screen.getByText(/Enter perceived effort to log this session\./)).toBeTruthy();
  });

  it("ONCE SUPPLIED, the entered values are what gets logged", async () => {
    storeDraft = { ...REAL_INITIAL_DRAFT, durationMin: 63, rpe: 9 };
    render(<LogWorkoutModal />);
    expect(applyBtn().disabled, "a complete draft must be submittable").toBe(false);
    fireEvent.click(applyBtn());
    await vi.waitFor(() => expect(logged.length).toBe(1));
    const b = logged[0] as unknown as Record<string, unknown>;
    // The athlete's numbers, not the sample ones.
    expect(b.duration_minutes).toBe(63);
    expect(b.session_rpe).toBe(9);
    expect(b.duration_minutes).not.toBe(COMPLETE_DRAFT.durationMin);
  });
});

// ---------------------------------------------------------------------------------
// THE RECOMMENDED WORKOUT PRE-FILLS AS TARGETS, NOT READINGS
//
// The defect this pins: the pre-fill asked /v1/next-session for goal "hybrid", which is
// not a TrainingGoal, so the backend answered 422 and the catch hid it — nothing ever
// pre-filled. Before that, it copied the prescribed kg and the RPE CAP into the set as if
// the athlete had entered them. Now it asks with the athlete's own goal, reads today's
// stored prescription back without re-prescribing, and nothing pre-filled reaches the log
// until the athlete confirms it.
// ---------------------------------------------------------------------------------

const SQUAT_RX: ExercisePrescription = {
  name: "Back Squat",
  sets: 5,
  reps: "3",
  prescribed_load_kg: 120,
  rpe_cap: 8,
  load_note: "86% of e1RM 140 kg",
};
const SQUAT_TARGET = "5 × 3 @ 120 kg · RPE ≤ 8";

/** Today's planned session and the issued revision GET /v1/planning/today serves for it. */
const REVISION_ID = 900;
const todaysSession = (id: number, exercises: ExercisePrescription[]): TodaySessionResponse =>
  ({
    session: { id, status: "pending" },
    prescription: { type: "Strength", focus: "x", rationale: "y", duration_min: 45, exercises },
    revision: { id: REVISION_ID, revision_no: 1, reason: "first_issue", safety_kind: "none", issued_at: "2026-10-04T00:00:00Z", issued_now: false },
  }) as unknown as TodaySessionResponse;

describe("the recommended workout pre-fills Log Workout", () => {
  const resetPrefill = () => {
    todayResponse = null;
    nextSessionRx = { exercises: [] };
    nextSessionGoals.length = 0;
    storeGoal = "Hypertrophy";
  };
  beforeEach(resetPrefill);
  afterEach(resetPrefill);

  it("asks for the athlete's own goal, never 'hybrid'", async () => {
    storeGoal = "Powerlifting";
    render(<LogWorkoutModal />);
    await vi.waitFor(() => expect(nextSessionGoals).toEqual(["Powerlifting"]));
  });

  it("shows today's issued revision, never a preview, when a session is planned", async () => {
    todayResponse = todaysSession(41, [SQUAT_RX]);
    render(<LogWorkoutModal />);
    expect(await screen.findByText(SQUAT_TARGET)).toBeTruthy();
    expect(screen.getByDisplayValue("Back Squat")).toBeTruthy();
    expect(nextSessionGoals, "an issued revision must not trigger a preview").toEqual([]);
  });

  it("falls back to a preview only when nothing is planned today", async () => {
    nextSessionRx = { exercises: [SQUAT_RX] };
    render(<LogWorkoutModal />);
    expect(await screen.findByText(SQUAT_TARGET)).toBeTruthy();
    expect(nextSessionGoals).toEqual(["Hypertrophy"]);
  });

  it("UNTOUCHED: a recommendation the athlete did not confirm is not logged", async () => {
    todayResponse = todaysSession(41, [SQUAT_RX]);
    render(<LogWorkoutModal />);
    await screen.findByText(SQUAT_TARGET);
    fireEvent.click(applyBtn());
    await vi.waitFor(() => expect(logged.length).toBe(1));
    const b = logged[0] as unknown as Record<string, unknown>;
    expect(hasKey(b, "sets"), "no set may be logged from an unconfirmed recommendation").toBe(false);
    expect(hasKey(b, "planned_session_id"), "an untouched plan is not fulfilled").toBe(false);
    expect(hasKey(b, "prescription_revision_id"), "nor is its revision claimed").toBe(false);
  });

  it("DONE AS PRESCRIBED logs the target reps and kg, never the RPE cap, and links the plan", async () => {
    todayResponse = todaysSession(41, [SQUAT_RX]);
    render(<LogWorkoutModal />);
    fireEvent.click(await screen.findByText(/Done as prescribed/));
    fireEvent.click(applyBtn());
    await vi.waitFor(() => expect(logged.length).toBe(1));
    const b = logged[0] as unknown as Record<string, unknown>;
    expect(b.sets).toEqual([
      expect.objectContaining({ free_text_name: "Back Squat", sets: 5, reps: 3, load_kg: 120, rpe: null }),
    ]);
    expect(b.planned_session_id).toBe(41);
    // P1: the log records exactly the revision the athlete was shown.
    expect(b.prescription_revision_id).toBe(REVISION_ID);
  });
});
