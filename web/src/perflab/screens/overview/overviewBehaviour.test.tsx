// @vitest-environment jsdom
//
// Behavioural guarantees that only a render can establish. The pure selector
// properties live in overviewModel.test.ts and the import-graph property in
// overviewBoundary.test.ts; what is proven here is what the COMPONENTS do:
//
//   - an authenticated athlete cannot reach the simulated session player (L3/#188)
//   - nothing auto-opens the check-in prompt (L4/#191)
//   - the guest preview labels itself as sample data (#185)
//
// Each of these was previously asserted only by reading the code, which is
// exactly the kind of claim that rots silently.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let token: string | null = null;
let todayResponse: unknown = { session: null, prescription: null };
const openCheckin = vi.fn();
const openAuth = vi.fn();
const openLog = vi.fn();
const setScreen = vi.fn();

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token, user: { email: "athlete@example.com" }, profile: null, email: "athlete@example.com", isGuest: token == null }),
}));

const storeState = {
  sessOpen: true,
  phaseIdx: 0,
  sessRemaining: 600,
  sessPhaseDurations: [600, 360],
  sessRunning: false,
  sessDone: false,
  readinessRefreshKey: 0,
  objectivesRefreshKey: 0,
  macrocyclesRefreshKey: 0,
  settings: { goal: "endurance" },
};

vi.mock("../../store", () => ({
  usePerfLab: () => ({
    state: storeState,
    actions: { openCheckin, openAuth, openLog, setScreen, closeSession: vi.fn(), sessToggle: vi.fn(), sessSkip: vi.fn(), sessToLog: vi.fn() },
  }),
  DEFAULT_GOAL: "endurance",
}));

// Every endpoint resolves empty/absent — the state in which the OLD Overview
// reached for fixtures, and the state in which a check-in prompt would fire.
vi.mock("@/api/perfLabClient", () => ({
  getReadiness: () => Promise.resolve({ score: null, wellness_delta: 0, components: [] }),
  listWellness: () => Promise.resolve([]),
  getStateHistory: () => Promise.resolve([]),
  listWorkouts: () => Promise.resolve([]),
  getDashboardOverview: () => Promise.resolve({ training_load: { acwr: null, status: "insufficient", sweet_spot_low: 0.8, sweet_spot_high: 1.3 }, adherence: { pct: null, streak_days: 0, window_days: 28 } }),
  getTodayPlannedSession: () => Promise.resolve(todayResponse),
  listObjectives: () => Promise.resolve([]),
  listMacrocycles: () => Promise.resolve([]),
}));

beforeEach(() => {
  token = null;
  openCheckin.mockClear();
  openAuth.mockClear();
});

afterEach(cleanup);

describe("the simulated session player is closed to authenticated athletes (L3 / #188)", () => {
  it("renders nothing for an authenticated athlete even when the session is open", async () => {
    token = "real-token";
    const { SessionPlayer } = await import("../../overlays/SessionPlayer");
    const { container } = render(<SessionPlayer />);
    // sessOpen is true in the mocked store, so the ONLY thing keeping the
    // fabricated interval plan off screen is the boundary guard.
    expect(storeState.sessOpen).toBe(true);
    expect(container.firstChild).toBeNull();
  });

  it("still renders for a guest, so the guard is about authority and not a blanket disable", async () => {
    token = null;
    const { SessionPlayer } = await import("../../overlays/SessionPlayer");
    const { container } = render(<SessionPlayer />);
    expect(container.firstChild).not.toBeNull();
  });
});

describe("no check-in prompt fires automatically (L4 / #191)", () => {
  it("does not open the check-in modal for an authenticated athlete with zero wellness rows", async () => {
    token = "real-token";
    const { AuthedOverview } = await import("./AuthedOverview");
    render(<AuthedOverview />);
    // Let every resource settle; the old code auto-opened once the wellness
    // resource resolved and its newest row was not "today".
    await screen.findByText(/This morning/i);
    await new Promise((r) => setTimeout(r, 0));
    expect(openCheckin).not.toHaveBeenCalled();
  });

  it("has no module-global prompt flag left anywhere in the source", async () => {
    const { readFileSync, readdirSync } = await import("node:fs");
    const { join } = await import("node:path");
    const root = join(__dirname, "..", "..", "..");
    const walk = (dir: string): string[] =>
      readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
        const full = join(dir, e.name);
        // Test files are excluded, exactly as the migration ratchet does — this
        // file names the retired flag in order to forbid it, and must not
        // convict itself.
        return e.isDirectory() ? walk(full) : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [full] : [];
      });
    const offenders = walk(root).filter((f) => /checkinPromptShown/.test(readFileSync(f, "utf8")));
    expect(offenders).toEqual([]);
  });
});

describe("the guest Overview labels itself as sample data (#185)", () => {
  it("renders a persistent sample-data label and the preview banner", async () => {
    token = null;
    const { GuestOverviewPreview } = await import("./GuestOverviewPreview");
    render(<GuestOverviewPreview />);
    expect(screen.getAllByText(/Sample data/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Preview — sample athlete/i)).toBeTruthy();
  });
});

describe("Overview insights show plan adjustments in words (S-A)", () => {
  it("lists visible labels, hides bookkeeping, and never prints an engine code", async () => {
    token = "real-token";
    todayResponse = {
      session: null,
      prescription: {
        type: "Strength",
        focus: "Back Squat + Bench Press",
        rationale: "Accumulation week.",
        duration_min: 60,
        exercises: [],
        why: {
          constraints_applied: ["block:benchmark", "static_with_safety_caps:arm"],
          constraint_details: [
            { code: "block:benchmark", label: "Benchmark session in your plan.", group: "block", athlete_visible: true },
            { code: "static_with_safety_caps:arm", label: "Fixed-template experiment arm.", group: "internal", athlete_visible: false },
          ],
          warnings: [],
        },
      },
    };
    try {
      const { AuthedOverview } = await import("./AuthedOverview");
      render(<AuthedOverview />);
      expect(await screen.findByText("Benchmark session in your plan.")).toBeTruthy();
      expect(screen.queryByText("Fixed-template experiment arm.")).toBeNull();
      expect(screen.queryByText(/block:benchmark/)).toBeNull();
    } finally {
      todayResponse = { session: null, prescription: null };
    }
  });
});
