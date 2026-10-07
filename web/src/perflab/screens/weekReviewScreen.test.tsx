// @vitest-environment jsdom
//
// Behavioural guarantees of the Week review screen that only a render can establish:
//
//   - the stat tiles read the backend's counts (adherence is the backend's number)
//   - an `insufficient` axis renders "not measured" with no value and no delta
//   - next-week items render their kind tag and reason; the empty list's copy does
//     not claim anything reviewed next week
//   - there are no Accept / Override buttons (no backend exists for them)
//   - every `available: false` reason renders honest copy, never numbers
//   - feedback for an unreviewed completed session goes through openFeedback(id, status)
//   - a guest sees a labelled sample and no request is made
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WeekReview } from "@/types";

let token: string | null = "real-token";
const openCheckin = vi.fn();
const openLog = vi.fn();
const openFeedback = vi.fn();
const openAuth = vi.fn();
const setScreen = vi.fn();

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token, user: { email: "athlete@example.com" }, profile: null, email: "athlete@example.com", isGuest: token == null }),
}));

vi.mock("../store", () => ({
  usePerfLab: () => ({
    state: { feedbackRefreshKey: 0 },
    actions: { openCheckin, openLog, openFeedback, openAuth, setScreen },
  }),
}));

let response: WeekReview;
const getWeekReview = vi.fn<(t: string, p?: unknown) => Promise<WeekReview>>(() => Promise.resolve(response));
vi.mock("@/api/perfLabClient", () => ({
  getWeekReview: (t: string, p?: unknown) => getWeekReview(t, p),
}));

const { WeekReviewScreen } = await import("./WeekReviewScreen");

// Values chosen so each asserted number collides with no other number on screen.
function liveReview(overrides: Partial<WeekReview> = {}): WeekReview {
  return {
    available: true,
    window: { block_id: 7, week_number: 2, duration_weeks: 6, start: "2026-09-21", end: "2026-09-27", is_current_week: true },
    counts: { planned: 5, completed: 3, skipped: 1, missed: 0, modified: 1, pending: 1, due: 4, adherence_pct: 75 },
    moved: {
      mean_fatigue_previous_week_start: 30,
      mean_fatigue_start: 34,
      mean_fatigue_end: 28,
      capacity: [
        { axis: "max_strength", measured: true, status_start: "established", status_end: "established", start: 60, end: 61.4, delta: 1.4 },
        { axis: "glycolytic", measured: false, status_start: "insufficient", status_end: "insufficient" },
      ],
    },
    sessions: [
      { planned_session_id: 101, scheduled_date: "2026-09-22", week_number: 2, category: "strength", modality: "lower", status: "completed", is_deload: false, is_benchmark: false, felt_rpe: 9, prescribed_rpe: 7.5, modified: false },
      { planned_session_id: 102, scheduled_date: "2026-09-24", week_number: 2, category: "endurance", modality: "run", status: "completed", is_deload: false, is_benchmark: false, felt_rpe: 5, prescribed_rpe: 5, feedback_status: "completed", followed_as_prescribed: true, modified: false },
      { planned_session_id: 103, scheduled_date: "2026-09-26", week_number: 2, category: "strength", modality: "upper", status: "pending", is_deload: false, is_benchmark: false, prescribed_rpe: null, modified: false },
    ],
    next_week: [
      { kind: "safety", source: "trigger:tissue_t.knee", title: "Knee tissue load high", reason: "tissue_t.knee is 46 (threshold 40)." },
      { kind: "plan", source: "block:deload_week", title: "Deload week", reason: "Week 3 is a scheduled deload (every 3 weeks)." },
    ],
    next_week_status: "changes_listed",
    ...overrides,
  };
}

beforeEach(() => {
  token = "real-token";
  response = liveReview();
  getWeekReview.mockClear();
  openFeedback.mockClear();
});

afterEach(cleanup);

describe("stat tiles read the backend's counts", () => {
  it("renders planned, completed and the backend's adherence", async () => {
    render(<WeekReviewScreen />);
    expect(await screen.findByText("75%")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();
    expect(screen.getByText("3 of 4 due so far")).toBeTruthy();
    expect(screen.getByText("1 skipped · 1 pending · 1 modified")).toBeTruthy();
    expect(screen.getByText("block wk 2 · 21–27 Sep")).toBeTruthy();
    // Mean fatigue at week end, with this week's and the prior week's movement.
    expect(screen.getByText("28")).toBeTruthy();
    expect(screen.getByText("−6.0 this week · +4.0 prior week")).toBeTruthy();
  });

  it("counts a missed session apart from skips and shows it as missed, not pending (P2)", async () => {
    const base = liveReview();
    response = liveReview({
      counts: { ...base.counts!, missed: 1 },
      sessions: [
        ...base.sessions!,
        { planned_session_id: 104, scheduled_date: "2026-09-23", week_number: 2, category: "endurance", modality: "run", status: "missed", is_deload: false, is_benchmark: false, prescribed_rpe: null, modified: false },
      ],
    });
    render(<WeekReviewScreen />);
    expect(await screen.findByText("1 skipped · 1 missed · 1 pending · 1 modified")).toBeTruthy();
    expect(screen.getByTestId("felt-104").textContent).toContain("missed · nothing logged");
    // P2b: a miss is an outcome the athlete may explain; the modal is told it is a miss.
    fireEvent.click(within(screen.getByTestId("felt-104")).getByRole("button", { name: "Add feedback" }));
    expect(openFeedback).toHaveBeenCalledWith(104, "missed");
    // A session still pending has no outcome: no feedback offered.
    expect(within(screen.getByTestId("felt-103")).queryByRole("button", { name: "Add feedback" })).toBeNull();
  });

  it("shows a dash, not 0%, when nothing is due yet", async () => {
    response = liveReview({ counts: { planned: 3, completed: 0, skipped: 0, missed: 0, modified: 0, pending: 3, due: 0, adherence_pct: null } });
    render(<WeekReviewScreen />);
    expect(await screen.findByText("nothing due yet this week")).toBeTruthy();
    expect(screen.queryByText("0%")).toBeNull();
  });
});

describe("what moved", () => {
  it("renders an insufficient axis as not measured, with no value and no delta", async () => {
    render(<WeekReviewScreen />);
    const glyco = await screen.findByTestId("moved-glycolytic");
    expect(within(glyco).getByText("not measured")).toBeTruthy();
    expect(glyco.textContent).not.toMatch(/\d/);

    const strength = screen.getByTestId("moved-max_strength");
    expect(within(strength).getByText("61.4")).toBeTruthy();
    expect(within(strength).getByText("+1.4")).toBeTruthy();
  });
});

describe("how it felt", () => {
  it("shows felt vs prescribed RPE and routes an unreviewed completed session to FeedbackModal", async () => {
    render(<WeekReviewScreen />);
    const tue = await screen.findByTestId("felt-101");
    expect(within(tue).getByText("prescribed RPE 7.5")).toBeTruthy();
    expect(within(tue).getByText("felt RPE 9")).toBeTruthy();
    expect(within(tue).getByText("above prescribed")).toBeTruthy();
    fireEvent.click(within(tue).getByRole("button", { name: "Add feedback" }));
    expect(openFeedback).toHaveBeenCalledWith(101, "completed");

    // Already reviewed: its note, no second feedback affordance.
    const thu = screen.getByTestId("felt-102");
    expect(within(thu).getByText(/followed as prescribed/)).toBeTruthy();
    expect(within(thu).queryByRole("button")).toBeNull();

    // Not done yet: nothing to report on (ADR-0070).
    const sat = screen.getByTestId("felt-103");
    expect(within(sat).queryByRole("button")).toBeNull();
    expect(within(sat).getByText("no RPE prescribed")).toBeTruthy();
  });
});

describe("what changes next week", () => {
  it("renders each determined fact with its kind tag and reason", async () => {
    render(<WeekReviewScreen />);
    const items = await screen.findAllByTestId("next-week-item");
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText("safety")).toBeTruthy();
    expect(within(items[0]).getByText("Knee tissue load high")).toBeTruthy();
    expect(within(items[0]).getByText("tissue_t.knee is 46 (threshold 40).")).toBeTruthy();
    expect(within(items[1]).getByText("plan")).toBeTruthy();
    expect(within(items[1]).getByText("Week 3 is a scheduled deload (every 3 weeks).")).toBeTruthy();
  });

  it("empty status copy does not claim a model reviewed next week", async () => {
    response = liveReview({ next_week: [], next_week_status: "nothing_scheduled_to_change" });
    render(<WeekReviewScreen />);
    const empty = await screen.findByTestId("next-week-empty");
    expect(empty.textContent).toMatch(/Nothing already scheduled changes next week/);
    expect(empty.textContent).not.toMatch(/review|checked|found nothing|looks good|no changes needed/i);
    expect(screen.queryAllByTestId("next-week-item")).toHaveLength(0);
  });
});

describe("no accept/override affordances exist", () => {
  it("renders no Accept or Override button, signed in or as a guest", async () => {
    render(<WeekReviewScreen />);
    await screen.findByText("75%");
    expect(screen.queryByRole("button", { name: /accept|override/i })).toBeNull();
    expect(screen.queryByText(/accept next week|override a change/i)).toBeNull();
    cleanup();

    token = null;
    render(<WeekReviewScreen />);
    expect(screen.queryByRole("button", { name: /accept|override/i })).toBeNull();
  });
});

describe("unavailable reasons render honest copy", () => {
  it.each([
    ["no_active_block", /No active training block/],
    ["no_state", /No twin state to review against/],
    ["state_invalid", /Your twin state couldn't be read/],
  ] as const)("%s", async (reason, copy) => {
    response = { available: false, reason };
    render(<WeekReviewScreen />);
    expect(await screen.findByText(copy)).toBeTruthy();
    expect(screen.queryByText("What moved")).toBeNull();
    expect(screen.queryByText(/%$/)).toBeNull();
  });

  it("offers Planning when there is no active block", async () => {
    response = { available: false, reason: "no_active_block" };
    render(<WeekReviewScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Go to Planning" }));
    expect(setScreen).toHaveBeenCalledWith("planning");
  });

  it("a failed load is an error, not an empty week", async () => {
    getWeekReview.mockImplementationOnce(() => Promise.reject({ message: "boom" }));
    render(<WeekReviewScreen />);
    expect(await screen.findByText("Couldn't load your week review")).toBeTruthy();
    expect(screen.queryByText("What moved")).toBeNull();
  });
});

describe("week stepping", () => {
  it("requests the neighbouring week of the same block", async () => {
    render(<WeekReviewScreen />);
    await screen.findByText("75%");
    expect(getWeekReview).toHaveBeenLastCalledWith("real-token", undefined);
    fireEvent.click(screen.getByRole("button", { name: "Previous week" }));
    await vi.waitFor(() => expect(getWeekReview).toHaveBeenLastCalledWith("real-token", { block_id: 7, week_number: 1 }));
  });
});

describe("guests", () => {
  it("see a labelled sample, the header has no Check in, and no request is made", async () => {
    token = null;
    render(<WeekReviewScreen />);
    expect(screen.getAllByText(/Sample data/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Preview — sample athlete/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Check in" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add feedback" })).toBeNull();
    expect(getWeekReview).not.toHaveBeenCalled();
  });
});
