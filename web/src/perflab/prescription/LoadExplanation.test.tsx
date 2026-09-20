// @vitest-environment jsdom
//
// N1: the explanation of a missing weight — its wording per reason, its link to Assess, and
// its place beside the exercise on BOTH screens that show the live prescription, under the
// effort guidance and never instead of it.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LoadExplanation as LoadExplanationData, WorkoutPrescription } from "@/types";
import { LoadExplanation } from "./LoadExplanation";

const setScreen = vi.fn();

const storeState = {
  settings: { goal: "Strength", units: "Metric (km)" },
  planningWeekAnchor: null,
  planningRefreshKey: 0,
  feedbackRefreshKey: 0,
  readinessRefreshKey: 0,
};

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token: "real-token", user: null, profile: null, email: null, isGuest: false }),
}));

vi.mock("../store", () => ({
  usePerfLab: () => ({
    state: storeState,
    actions: {
      setScreen,
      openLog: vi.fn(),
      openBlockCreate: vi.fn(),
      openFeedback: vi.fn(),
      focusPlanningWeek: vi.fn(),
    },
  }),
}));

function explanation(over: Partial<LoadExplanationData>): LoadExplanationData {
  return {
    status: "no_qualifying_evidence",
    reason: null,
    benchmark_code: "pl_e1rm_squat",
    evaluated_at: "2026-09-14T12:00:00Z",
    evidence_performed_at: null,
    ...over,
  };
}

const STALE_SQUAT = "No recent qualifying squat evidence. Use the prescribed effort guidance today.";

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const RX: WorkoutPrescription = {
  type: "Strength",
  focus: "Heavy Lower",
  rationale: "Readiness is high.",
  duration_min: 60,
  // Phase 2.2: the server always sends these — they are computed from `structure`, so the
  // generated type marks them required. Null here: this fixture carries no structure.
  calculated_duration_min: null,
  duration_estimate: null,
  model_version: "v0.3",
  exercises: [
    {
      name: "Back Squat",
      sets: 3,
      reps: "5",
      load_note: "Autoregulate by RPE",
      weak_point_tags: [],
      load_explanation: explanation({ reason: "stale", evidence_performed_at: "2026-08-01T00:00:00Z" }),
    },
  ],
  why: null,
};

vi.mock("@/api/perfLabClient", () => ({
  getNextSession: () => Promise.resolve(RX),
  // Never settles: the surrounding cards stay in their loading state and stay out of the way.
  getReadiness: () => new Promise(() => {}),
  getStateHistory: () => new Promise(() => {}),
  listPlannedSessions: () =>
    Promise.resolve([
      {
        id: 1, block_id: 1, user_id: 1, scheduled_date: todayIso(), original_scheduled_date: null,
        week_number: 1, day_of_week: 1, category: "Heavy Lower", modality: "Strength", status: "pending",
        is_deload: false, is_benchmark: false, benchmark_key: null, prescribed_content: null,
        workout_log_id: null, completed_at: null,
      },
    ]),
}));

beforeEach(() => setScreen.mockClear());
afterEach(cleanup);

describe("the wording for each reason", () => {
  it.each([
    ["stale", STALE_SQUAT, "Review strength history"],
    ["missing_performance_date", "We need the performance date before this report can guide a weight.", "Review strength history"],
    ["estimate_not_used", "Your estimate is saved, but it isn't used to recommend weights.", "Review strength history"],
    ["set_not_qualifying", "This set is saved, but it doesn't qualify for a weight recommendation.", "Review strength history"],
    ["no_evidence", "No qualifying strength history yet. Use the prescribed effort guidance.", "Report a previous performance"],
    [
      "not_qualifying",
      "Your saved strength history doesn't qualify for a weight recommendation yet. Use the prescribed effort guidance.",
      "Review strength history",
    ],
  ] as const)("%s", (reason, text, link) => {
    const onOpenAssess = vi.fn();
    render(<LoadExplanation explanation={explanation({ reason })} exerciseName="Back Squat" onOpenAssess={onOpenAssess} />);
    expect(screen.getByText(text)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: link }));
    expect(onOpenAssess).toHaveBeenCalledTimes(1);
  });

  it("names the canonical lift by its label, and any other lift by the exercise name", () => {
    render(
      <>
        <LoadExplanation explanation={explanation({ reason: "stale", benchmark_code: "pl_e1rm_bench" })} exerciseName="Bench Press" onOpenAssess={vi.fn()} />
        <LoadExplanation explanation={explanation({ reason: "stale", benchmark_code: "other_code" })} exerciseName="Front Squat" onOpenAssess={vi.fn()} />
      </>,
    );
    expect(screen.getByText("No recent qualifying bench press evidence. Use the prescribed effort guidance today.")).toBeTruthy();
    expect(screen.getByText("No recent qualifying Front Squat evidence. Use the prescribed effort guidance today.")).toBeTruthy();
  });

  it("never shows an age, even when the evidence date is known", () => {
    const { container } = render(
      <LoadExplanation explanation={explanation({ reason: "stale", evidence_performed_at: "2026-08-01T00:00:00Z" })} exerciseName="Back Squat" onOpenAssess={vi.fn()} />,
    );
    expect(container.textContent).not.toMatch(/\bdays?\b|\bago\b|2026/);
  });

  it("an unsupported exercise is told so, with no link and nothing about history", () => {
    const { container } = render(
      <LoadExplanation explanation={explanation({ status: "not_supported", benchmark_code: null })} exerciseName="Dumbbell Row" onOpenAssess={vi.fn()} />,
    );
    expect(screen.getByText("Weight recommendations aren't available for this exercise. Use the prescribed effort guidance.")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(container.textContent).not.toMatch(/history|report|test/i);
  });

  it("says nothing when a weight was recommended, or when there is nothing to explain", () => {
    const { container } = render(
      <>
        <LoadExplanation explanation={explanation({ status: "recommended" })} exerciseName="Back Squat" onOpenAssess={vi.fn()} />
        <LoadExplanation explanation={null} exerciseName="Push-up" onOpenAssess={vi.fn()} />
      </>,
    );
    expect(container.textContent).toBe("");
  });
});

describe.each([
  ["Planning", async () => (await import("../screens/PlanningScreen")).PlanningScreen],
  ["Digital Twin", async () => (await import("../screens/TwinScreen")).TwinScreen],
])("%s shows the explanation beside the prescribed exercise", (_screenName, load) => {
  it("keeps the effort guidance, adds the explanation, and links to Assess", async () => {
    const Component = await load();
    render(<Component />);

    expect(await screen.findByText(STALE_SQUAT)).toBeTruthy();
    expect(screen.getByText("3×5 · Autoregulate by RPE")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Review strength history" }));
    expect(setScreen).toHaveBeenCalledWith("assess");
  });
});
