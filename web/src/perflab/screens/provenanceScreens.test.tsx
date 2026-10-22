// @vitest-environment jsdom
//
// INT-15 2C, at render level. Every consumer of /v1/state-history, and the onboarding banner:
//
//   - a coded `display_state_unavailable` refusal renders as UNAVAILABLE: not empty, not a
//     retryable load failure, and never an older snapshot standing in for the current one
//   - a recovered (degraded) snapshot never shows a value without the recovered-state warning,
//     and its confidence is "not measured" (no established / provisional / insufficient band)
//   - a mixed window marks or counts its recovered points
//   - a canonical window shows no warning at all
//   - onboarding: seeded-but-unreadable state takes precedence over "provisional"
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MESSAGE =
  "Your training-state history can't be shown right now: a stored snapshot can't be read. It needs a repair on our side; nothing you change here will fix it.";
const WARNING = "Recovered estimate: rebuilt from older stored values; confidence not measured.";

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token: "real-token", user: { email: "a@example.com" }, profile: null, email: "a@example.com", isGuest: false }),
}));

const storeState = {
  readinessRefreshKey: 0,
  workoutsRefreshKey: 0,
  objectivesRefreshKey: 0,
  macrocyclesRefreshKey: 0,
  selectedTwinSnapshotId: null as string | null,
  settings: { goal: "endurance", units: "Metric (km)" },
};
vi.mock("../store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../store")>();
  return {
    ...actual,
    usePerfLab: () => ({
      state: storeState,
      actions: {
        openCheckin: vi.fn(), openLog: vi.fn(), openAuth: vi.fn(), setScreen: vi.fn(),
        setSelectedTwinSnapshot: vi.fn(), setTwinDay: vi.fn(), openExplain: vi.fn(),
      },
    }),
  };
});

const fatigue = (v: number) => ({ cns: v, muscular: v, metabolic: v, structural: v, tendon: v, grip: v });
const daysAgo = (d: number) => new Date(Date.now() - d * 864e5).toISOString();
const ESTABLISHED = { aerobic: "established", glycolytic: "established", max_strength: "established", power: "established", work_capacity: "established" };
const PROVISIONAL = { aerobic: "provisional", glycolytic: "insufficient", max_strength: "provisional", power: "insufficient", work_capacity: "provisional" };
const snap = (id: string, d: number, f: number, recovered: boolean) => ({
  snapshot_id: id,
  timestamp: daysAgo(d),
  fatigue_f: fatigue(f),
  tissue_t: { knee: 1, lumbar: 1, hip: 1, ankle: 1, shoulder: 1, elbow: 1, wrist: 1, finger: 1 },
  capacity_x: { aerobic: 400, glycolytic: 51, max_strength: 62, power: 53, work_capacity: 64 },
  // A recovered row carries the weak prior's statuses; they must never be shown as a band.
  capacity_confidence_status: recovered ? PROVISIONAL : ESTABLISHED,
  habit_strength: 0.5,
  s_struct_signal: 1,
  state_source: recovered ? "legacy_recovery" : "canonical",
  degraded: recovered,
  degradation_reason: recovered ? "null_engine_state_legacy_row" : null,
  codec_version: "v2",
});

const UNAVAILABLE = { message: MESSAGE, status: 409, code: "display_state_unavailable" };
let history: () => Promise<unknown> = () => Promise.resolve([]);
let onboarding: () => Promise<unknown> = () => Promise.reject(new Error("not under test"));

vi.mock("@/api/perfLabClient", () => ({
  getStateHistory: () => history(),
  getReadiness: () => Promise.resolve({ score: 70, wellness_delta: 0, components: [] }),
  getNextSession: () => Promise.reject(new Error("no prescription")),
  listWorkouts: () => Promise.resolve([]),
  listBenchmarkObservations: () => Promise.resolve([]),
  listWellness: () => Promise.resolve([]),
  getDashboardOverview: () =>
    Promise.resolve({
      training_load: { acwr: null, status: "insufficient", sweet_spot_low: 0.8, sweet_spot_high: 1.3 },
      adherence: { pct: null, streak_days: 0, window_days: 28 },
    }),
  getTodayPlannedSession: () => Promise.resolve({ session: null, prescription: null }),
  listObjectives: () => Promise.resolve([]),
  getDrivingObjective: () => Promise.resolve({ objective_id: null, source: null }),
  listMacrocycles: () => Promise.resolve([]),
  getAssessmentSurface: () =>
    Promise.resolve({ active_domains: [], groups: [], mode: "onramp", policy_version: "t", recommended: [] }),
  getOnboardingState: () => onboarding(),
  completeOnboarding: vi.fn(),
  submitBenchmarkObservation: vi.fn(),
  submitStrengthEvidence: vi.fn(),
}));

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const { TwinScreen } = await import("./TwinScreen");
const { HistoryScreen } = await import("./HistoryScreen");
const { AuthedOverview } = await import("./overview/AuthedOverview");
const { AssessmentSurfaceScreen } = await import("./AssessmentSurfaceScreen");

beforeEach(() => {
  storeState.selectedTwinSnapshotId = null;
  history = () => Promise.resolve([]);
  onboarding = () => Promise.reject(new Error("not under test"));
});
afterEach(cleanup);

const BAND_WORDS = /need more evidence|Not enough evidence|provisional \(estimate\)|confidence-scored/;

describe("Twin", () => {
  it("unavailable history renders the unavailable card, with no retry prompt", async () => {
    history = () => Promise.reject(UNAVAILABLE);
    render(<TwinScreen />);
    expect(await screen.findByText("Twin state unavailable")).toBeTruthy();
    expect(screen.getByText(MESSAGE)).toBeTruthy();
    expect(screen.queryByText(/Reload to try again/)).toBeNull();
    expect(screen.queryByText("No twin state yet")).toBeNull();
    // The header says so too: unreadable stored state is not "no recorded state".
    expect(screen.getByText("State unavailable")).toBeTruthy();
    expect(screen.queryByText("No recorded state")).toBeNull();
  });

  it("an ordinary failure keeps the header's 'No recorded state'", async () => {
    history = () => Promise.reject({ message: "Network down", status: 0 });
    render(<TwinScreen />);
    expect(await screen.findByText("Couldn't load your twin")).toBeTruthy();
    expect(screen.getByText("No recorded state")).toBeTruthy();
    expect(screen.queryByText("State unavailable")).toBeNull();
  });

  it("a recovered head is viewed with the warning and no confidence band", async () => {
    history = () => Promise.resolve([snap("a", 2, 30, false), snap("b", 1, 40, true)]);
    render(<TwinScreen />);
    expect(await screen.findByText(`Viewing a recovered snapshot. ${WARNING}`)).toBeTruthy();
    expect(screen.getByText("recovered estimate · confidence not measured")).toBeTruthy();
    expect(screen.getByTestId("capacity-view").textContent ?? "").not.toMatch(BAND_WORDS);
  });

  it("a canonical head in a mixed window notes the recovered points instead", async () => {
    history = () => Promise.resolve([snap("a", 2, 30, true), snap("b", 1, 40, false)]);
    render(<TwinScreen />);
    expect(
      await screen.findByText("1 of 2 points are recovered estimates; confidence not measured for those."),
    ).toBeTruthy();
    expect(screen.queryByText(/Viewing a recovered snapshot/)).toBeNull();
    expect(screen.getByText("5 axes · confidence-scored")).toBeTruthy();
  });

  it("a canonical window shows no warning", async () => {
    history = () => Promise.resolve([snap("a", 2, 30, false), snap("b", 1, 40, false)]);
    render(<TwinScreen />);
    await screen.findByText("5 axes · confidence-scored");
    expect(screen.queryAllByTestId("recovered-note")).toEqual([]);
  });
});

describe("History", () => {
  it("unavailable history renders unavailable, not 'not enough history'", async () => {
    history = () => Promise.reject(UNAVAILABLE);
    render(<HistoryScreen />);
    // Both history cards (trend + aerobic) show the refusal; a "note" notice renders its body only.
    expect((await screen.findAllByText(MESSAGE)).length).toBe(2);
    expect(screen.queryByText(/Not enough history/)).toBeNull();
    expect(screen.queryByText(/try again/)).toBeNull();
  });

  it("marks each recovered point and notes the window", async () => {
    history = () => Promise.resolve([snap("a", 3, 30, false), snap("b", 2, 40, true), snap("c", 1, 50, false)]);
    const { container } = render(<HistoryScreen />);
    expect(
      await screen.findByText("1 of 3 points are recovered estimates (marked); confidence not measured for those."),
    ).toBeTruthy();
    await vi.waitFor(() => expect(container.querySelectorAll('[data-testid="recovered-point"]').length).toBe(1));
  });

  it("a recovered latest snapshot carries the warning on the aerobic card", async () => {
    history = () => Promise.resolve([snap("a", 2, 30, false), snap("b", 1, 40, true)]);
    render(<HistoryScreen />);
    expect(await screen.findByText(WARNING)).toBeTruthy();
  });

  it("a canonical window shows no warning", async () => {
    history = () => Promise.resolve([snap("a", 2, 30, false), snap("b", 1, 40, false)]);
    const { container } = render(<HistoryScreen />);
    await screen.findByText("click a point to time-travel");
    await vi.waitFor(() => expect(container.querySelectorAll("circle.cursor-pointer").length).toBe(2));
    expect(screen.queryAllByTestId("recovered-note")).toEqual([]);
    expect(container.querySelectorAll('[data-testid="recovered-point"]').length).toBe(0);
  });
});

describe("Overview", () => {
  it("unavailable history renders the unavailable message, not 'no state yet'", async () => {
    history = () => Promise.reject(UNAVAILABLE);
    render(<AuthedOverview />);
    expect((await screen.findAllByText(MESSAGE)).length).toBe(2); // trend + twin snapshot
    expect(screen.queryByText(/No twin state yet/)).toBeNull();
    expect(screen.queryByText(/Couldn't load your history/)).toBeNull();
  });

  it("a recovered newest snapshot carries the warning, and a mixed trend counts its points", async () => {
    history = () => Promise.resolve([snap("a", 3, 30, false), snap("b", 2, 40, true), snap("c", 1, 50, true)]);
    render(<AuthedOverview />);
    expect(await screen.findByText(WARNING)).toBeTruthy();
    expect(screen.getByText("2 of 3 points are recovered estimates; confidence not measured for those.")).toBeTruthy();
  });

  it("a canonical window shows no warning", async () => {
    history = () => Promise.resolve([snap("a", 2, 30, false), snap("b", 1, 40, false)]);
    render(<AuthedOverview />);
    await screen.findByText(/Fatigue-derived trend/);
    await vi.waitFor(() => expect(screen.queryByText("…")).toBeNull());
    expect(screen.queryAllByTestId("recovered-note")).toEqual([]);
  });
});

describe("Onboarding banner", () => {
  const state = (twin: Record<string, unknown>) =>
    Promise.resolve({
      status: "in_progress",
      completed_reason: null,
      can_prescribe: true,
      missing_basics: [],
      is_minor: false,
      measurement_debt: [],
      twin: { seed_status: "experience_prior_only", ...twin },
    });

  it("unreadable seeded state takes precedence over provisional, with no confidence band", async () => {
    onboarding = () => state({ seeded: true, provisional: true, overall_confidence: "insufficient", state_available: false });
    render(<AssessmentSurfaceScreen />);
    expect(await screen.findByText("state needs attention")).toBeTruthy();
    expect(screen.queryByText("provisional")).toBeNull();
    expect(screen.queryByText(/overall confidence/)).toBeNull();
    expect(screen.getByRole("button", { name: "I’m done for now" })).toBeTruthy(); // never a gate
  });

  it("readable provisional state still reads provisional", async () => {
    onboarding = () => state({ seeded: true, provisional: true, overall_confidence: "provisional", state_available: true });
    render(<AssessmentSurfaceScreen />);
    expect(await screen.findByText("provisional")).toBeTruthy();
    expect(screen.queryByText("state needs attention")).toBeNull();
  });

  it("absent state stays 'not seeded'", async () => {
    onboarding = () => state({ seeded: false, provisional: true, overall_confidence: null, state_available: false });
    render(<AssessmentSurfaceScreen />);
    expect(await screen.findByText("not seeded")).toBeTruthy();
    expect(screen.queryByText("state needs attention")).toBeNull();
  });
});
