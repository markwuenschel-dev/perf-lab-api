// @vitest-environment jsdom
//
// Behavioural guarantees of the live Planning screen that only a render can
// establish:
//
//   - the forward projection renders load and MODELED FATIGUE (never readiness)
//     and labels template-estimate sessions as estimates
//   - an unavailable projection explains itself and never draws a chart
//   - drag-to-reschedule (and its +1 day twin) PATCHes the dragged session's own
//     id with the dropped day's date; illegal drops write nothing
//   - Mark skipped PATCHes the missed session's status
//   - logged cells read done / missed / pending (today) / upcoming / rest off the
//     real session status and date
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PlannedSessionRead, PlannedWeekProjection } from "@/types";

let token: string | null = "real-token";
const openLog = vi.fn();
const openCheckin = vi.fn();
const openFeedback = vi.fn();

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token, user: null, profile: null, email: null, isGuest: token == null }),
}));

const storeState = {
  settings: { goal: "strength", units: "Metric (km)" },
  planningWeekAnchor: null,
  planningRefreshKey: 0,
  feedbackRefreshKey: 0,
  readinessRefreshKey: 0,
};

vi.mock("../store", () => ({
  usePerfLab: () => ({
    state: storeState,
    actions: {
      openLog,
      openCheckin,
      openFeedback,
      openBlockCreate: vi.fn(),
      focusPlanningWeek: vi.fn(),
      setScreen: vi.fn(),
      openExplain: vi.fn(),
      openSession: vi.fn(),
    },
  }),
}));

// "Today" is Wednesday 30 Sep 2026: the displayed week is Mon 28 Sep … Sun 4 Oct.
const WEEK_START = "2026-09-28";

const session = (over: Partial<PlannedSessionRead>): PlannedSessionRead => ({
  id: 0,
  block_id: 7,
  user_id: 1,
  scheduled_date: WEEK_START,
  original_scheduled_date: null,
  week_number: 3,
  day_of_week: 1,
  category: "strength",
  modality: "strength",
  status: "pending",
  is_deload: false,
  is_benchmark: false,
  benchmark_key: null,
  prescribed_content: null,
  workout_log_id: null,
  completed_at: null,
  ...over,
});

const WEEK: PlannedSessionRead[] = [
  session({ id: 11, scheduled_date: "2026-09-28", status: "completed", workout_log_id: 501, category: "recovery", modality: "running" }),
  session({ id: 12, scheduled_date: "2026-09-29", category: "upper" }),
  session({ id: 13, scheduled_date: "2026-09-30", category: "hybrid" }),
  session({ id: 14, scheduled_date: "2026-10-02", category: "tempo", modality: "running" }),
];

const BLOCK_SESSIONS: PlannedSessionRead[] = [
  session({ id: 90, week_number: 4, is_deload: true }),
  session({ id: 91, week_number: 8, is_benchmark: true }),
];

const WORKOUTS = [
  {
    id: 501, modality: "running", duration_minutes: 44, session_rpe: 5, distance_meters: 8200, total_volume_load: 0,
    is_benchmark: false, logged_at: "2026-09-28T08:00:00", session_timestamp: "2026-09-28T07:00:00",
  },
];

const AVAILABLE: PlannedWeekProjection = {
  available: true,
  reason: null,
  window: { start: "2026-09-30", end: "2026-10-04", block_id: 7, week_number: 3 },
  days: [
    { date: "2026-09-30", sessions: [{ planned_session_id: 13, modality: "hybrid", basis: "prescribed", load: 300 }], load: 300, mean_fatigue: 36, fatigue: { cns: 36, muscular: 36, metabolic: 36, structural: 36, tendon: 36, grip: 36 } },
    { date: "2026-10-01", sessions: [], load: 0, mean_fatigue: 33, fatigue: { cns: 33, muscular: 33, metabolic: 33, structural: 33, tendon: 33, grip: 33 } },
    { date: "2026-10-02", sessions: [{ planned_session_id: 14, modality: "running", basis: "template_estimate", load: 210 }], load: 210, mean_fatigue: 38, fatigue: { cns: 38, muscular: 38, metabolic: 38, structural: 38, tendon: 38, grip: 38 } },
  ],
  peak_mean_fatigue: 38,
};

let projection: PlannedWeekProjection = AVAILABLE;
const updatePlannedSession = vi.fn((id: number) => Promise.resolve(session({ id })));
const getPlannedWeekProjection = vi.fn(() => Promise.resolve(projection));

vi.mock("@/api/perfLabClient", () => ({
  listPlannedSessions: (_t: string, params?: { start_date?: string }) =>
    Promise.resolve(params?.start_date === WEEK_START ? WEEK : BLOCK_SESSIONS),
  listPlanningBlocks: () =>
    Promise.resolve([{ id: 7, goal: "strength", status: "active", start_date: "2026-09-14", end_date: null, duration_weeks: 8 }]),
  listWorkouts: () => Promise.resolve(WORKOUTS),
  getPlannedWeekProjection: (...args: unknown[]) => getPlannedWeekProjection(...(args as [])),
  updatePlannedSession: (...args: unknown[]) => updatePlannedSession(...(args as [number])),
  getReadiness: () => Promise.resolve({ score: 71, band: "moderate", components: [] }),
  getNextSession: () => Promise.reject(new Error("no prescription")),
}));

// The viz Chart sizes itself with ResizeObserver, which jsdom does not provide.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const { PlanningScreen } = await import("./PlanningScreen");

const cell = (container: HTMLElement, iso: string, slot: "planned" | "logged") =>
  container.querySelector<HTMLElement>(`[data-day="${iso}"][data-slot="${slot}"]`)!;

beforeEach(() => {
  // Fake only Date: promise/timer scheduling stays real so findBy* can poll.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 8, 30, 12, 0, 0));
  token = "real-token";
  projection = AVAILABLE;
  updatePlannedSession.mockClear();
  getPlannedWeekProjection.mockClear();
  openLog.mockClear();
  openFeedback.mockClear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("forward projection", () => {
  it("renders load and modeled fatigue for the displayed week and labels estimates", async () => {
    render(<PlanningScreen />);
    expect(await screen.findByText("load 300 · fatigue 36")).toBeTruthy();
    expect(screen.getByText("load 210 · fatigue 38")).toBeTruthy();
    // Only the template-estimate day carries the estimate tag — and it is that day's row.
    expect(screen.getAllByText("estimate")).toHaveLength(1);
    const row = (text: string) => screen.getByText(text).closest("li")!;
    expect(within(row("load 210 · fatigue 38")).queryByText("estimate")).toBeTruthy();
    expect(within(row("load 300 · fatigue 36")).queryByText("estimate")).toBeNull();
    expect(screen.getByText(/1 of 2 sessions is a template estimate/)).toBeTruthy();
    expect(screen.getByRole("img", { name: "Projected daily training load" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Projected modeled mean fatigue" })).toBeTruthy();
    expect(screen.getByText(/Modeled fatigue, not readiness/)).toBeTruthy();
    // The displayed week's Sunday is asked for explicitly.
    expect(getPlannedWeekProjection).toHaveBeenCalledWith("real-token", "2026-10-04");
  });

  it("explains an unavailable projection and never draws a chart", async () => {
    // Days are present on purpose: `available: false` alone must suppress the
    // chart, whatever else the payload happens to carry.
    projection = { ...AVAILABLE, available: false, reason: "no_state" };
    render(<PlanningScreen />);
    expect(await screen.findByText(/no modeled state to project from yet/)).toBeTruthy();
    expect(screen.queryByRole("img", { name: "Projected daily training load" })).toBeNull();
    expect(screen.queryByRole("img", { name: "Projected modeled mean fatigue" })).toBeNull();
  });

  it("says so when the stored state cannot be read strictly", async () => {
    projection = { available: false, reason: "state_invalid", window: { start: "2026-09-30", end: "2026-10-04" }, days: [] };
    render(<PlanningScreen />);
    expect(await screen.findByText(/couldn't be read cleanly/)).toBeTruthy();
    expect(screen.queryByRole("img", { name: "Projected modeled mean fatigue" })).toBeNull();
  });
});

describe("rescheduling", () => {
  it("drag-drops a pending session onto an open future day", async () => {
    const { container } = render(<PlanningScreen />);
    await screen.findByText("Tempo");
    fireEvent.dragStart(cell(container, "2026-10-02", "planned"));
    fireEvent.dragOver(cell(container, "2026-10-03", "planned"));
    fireEvent.drop(cell(container, "2026-10-03", "planned"));
    expect(updatePlannedSession).toHaveBeenCalledTimes(1);
    expect(updatePlannedSession).toHaveBeenCalledWith(14, { scheduled_date: "2026-10-03" }, "real-token");
  });

  it("writes nothing when the drop day already has a session", async () => {
    const { container } = render(<PlanningScreen />);
    await screen.findByText("Tempo");
    fireEvent.dragStart(cell(container, "2026-10-02", "planned"));
    fireEvent.drop(cell(container, "2026-09-30", "planned"));
    expect(updatePlannedSession).not.toHaveBeenCalled();
  });

  it("does not let a completed session be dragged", async () => {
    const { container } = render(<PlanningScreen />);
    await screen.findByText("Tempo");
    expect(cell(container, "2026-09-28", "planned").getAttribute("draggable")).toBe("false");
    expect(cell(container, "2026-10-02", "planned").getAttribute("draggable")).toBe("true");
  });

  it("moves a session one day with the keyboard-reachable +1 day button", async () => {
    render(<PlanningScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Move Tempo to Sat" }));
    expect(updatePlannedSession).toHaveBeenCalledWith(14, { scheduled_date: "2026-10-03" }, "real-token");
  });

  it("marks a missed session skipped by its own id", async () => {
    render(<PlanningScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Mark skipped" }));
    expect(updatePlannedSession).toHaveBeenCalledWith(12, { status: "skipped" }, "real-token");
  });
});

describe("the week grid", () => {
  it("reads each logged cell's state off the real session status and date", async () => {
    const { container } = render(<PlanningScreen />);
    await screen.findByText("Tempo");
    const state = (iso: string) => cell(container, iso, "logged").getAttribute("data-state");
    expect(state("2026-09-28")).toBe("done");
    expect(state("2026-09-29")).toBe("missed");
    expect(state("2026-09-30")).toBe("pending");
    expect(state("2026-10-01")).toBe("rest");
    expect(state("2026-10-02")).toBe("upcoming");
    expect(state("2026-10-04")).toBe("rest");

    // done shows the fulfilling workout's own numbers (sRPE load = 5 × 44)
    const done = within(cell(container, "2026-09-28", "logged"));
    expect(done.getByText("Running · 44 min")).toBeTruthy();
    expect(done.getByText(/RPE 5 · 8\.2 km/)).toBeTruthy();
    expect(done.getByText(/load 220/)).toBeTruthy();

    // today: TODAY badge on the planned half, Log workout on the logged half
    expect(within(cell(container, "2026-09-30", "planned")).getByText("TODAY")).toBeTruthy();
    fireEvent.click(within(cell(container, "2026-09-30", "logged")).getByRole("button", { name: "Log workout" }));
    expect(openLog).toHaveBeenCalledTimes(1);

    // missed: the skip action lives only on the missed cell
    expect(within(cell(container, "2026-09-29", "logged")).getByRole("button", { name: "Mark skipped" })).toBeTruthy();
    expect(within(cell(container, "2026-10-02", "logged")).queryByRole("button")).toBeNull();
  });

  it("shows the block cadence from the sessions' own deload/benchmark flags", async () => {
    render(<PlanningScreen />);
    expect(await screen.findByText("Strength block")).toBeTruthy();
    expect(screen.getByText("3 / 8")).toBeTruthy();
    expect(screen.getByText(/deload · wk 4 \(next week\)/)).toBeTruthy();
    expect(screen.getByText(/benchmark week · wk 8/)).toBeTruthy();
  });
});

describe("guest preview", () => {
  it("is labelled sample data and offers no writes", () => {
    token = null;
    const { container } = render(<PlanningScreen />);
    expect(screen.getByText("Preview — sample data.")).toBeTruthy();
    expect(screen.getByText("Forward projection · sample")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Check in" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Mark skipped" })).toBeNull();
    expect(container.querySelector('[draggable="true"]')).toBeNull();
    expect(getPlannedWeekProjection).not.toHaveBeenCalled();
  });
});
