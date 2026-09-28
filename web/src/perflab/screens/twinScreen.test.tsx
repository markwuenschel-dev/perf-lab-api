// @vitest-environment jsdom
//
// Behavioural guarantees of the live Digital Twin that only a render can
// establish:
//
//   - the canonical readiness score is shown for the LATEST snapshot only; a
//     historical snapshot gets a neutral ring, the honest "not recorded" copy
//     and a separately labelled mean fatigue — never a fabricated readiness
//   - the scrubber steps by snapshot_id, not by list index
//   - the header's Check in is offered to a signed-in athlete, not a guest
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let token: string | null = "real-token";
const openCheckin = vi.fn();
const openLog = vi.fn();
const setSelectedTwinSnapshot = vi.fn();

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token, user: { email: "athlete@example.com" }, profile: null, email: "athlete@example.com", isGuest: token == null }),
}));

const storeState: { readinessRefreshKey: number; selectedTwinSnapshotId: string | null; settings: { goal: string } } = {
  readinessRefreshKey: 0,
  selectedTwinSnapshotId: null,
  settings: { goal: "strength" },
};

vi.mock("../store", () => ({
  usePerfLab: () => ({
    state: storeState,
    actions: { openCheckin, openLog, setSelectedTwinSnapshot, setScreen: vi.fn(), setTwinDay: vi.fn(), openExplain: vi.fn() },
  }),
}));

// Values chosen so the readiness score (83) collides with no other number on screen.
const snapshot = (id: string, timestamp: string, fatigue: number) => ({
  snapshot_id: id,
  timestamp,
  fatigue_f: { cns: fatigue, muscular: fatigue, metabolic: fatigue, structural: fatigue, tendon: fatigue, grip: fatigue },
  tissue_t: { knee: 21, lumbar: 22, hip: 23, ankle: 24, shoulder: 25, elbow: 26, wrist: 27, finger: 28 },
  capacity_x: { aerobic: 410, glycolytic: 51, max_strength: 62, power: 53, work_capacity: 64 },
  capacity_confidence_status: { aerobic: "established", glycolytic: "established", max_strength: "established", power: "established", work_capacity: "established" },
  habit_strength: 0.66,
  s_struct_signal: 1.4,
});

const ROWS = [snapshot("snap-old", "2026-09-20T08:00:00Z", 40), snapshot("snap-new", "2026-09-27T08:00:00Z", 30)];

vi.mock("@/api/perfLabClient", () => ({
  getStateHistory: () => Promise.resolve(ROWS),
  getReadiness: () => Promise.resolve({ score: 83, wellness_delta: 0, components: [] }),
  getNextSession: () => Promise.reject(new Error("no prescription")),
}));

// The viz Chart sizes itself with ResizeObserver, which jsdom does not provide.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const { TwinScreen } = await import("./TwinScreen");

beforeEach(() => {
  token = "real-token";
  storeState.selectedTwinSnapshotId = null;
  openCheckin.mockClear();
  setSelectedTwinSnapshot.mockClear();
});

afterEach(cleanup);

describe("readiness is shown for the latest snapshot only", () => {
  it("renders the canonical score on the latest snapshot", async () => {
    render(<TwinScreen />);
    expect(await screen.findByText("83")).toBeTruthy();
    expect(screen.queryByText(/was not recorded for this snapshot/)).toBeNull();
  });

  it("goes neutral on a historical snapshot and never shows the score", async () => {
    storeState.selectedTwinSnapshotId = "snap-old";
    render(<TwinScreen />);
    expect(await screen.findByText("Wellness-adjusted readiness was not recorded for this snapshot.")).toBeTruthy();
    // The ring's "—" (the oldest row's Struct. signal trend is also "—").
    expect(screen.getAllByText("—").some((el) => el.className.includes("text-[26px]"))).toBe(true);
    expect(screen.getByText("Mean fatigue · 40 / 100")).toBeTruthy();
    expect(screen.queryByText("83")).toBeNull();
  });
});

describe("the scrubber", () => {
  it("steps back by snapshot_id from the latest row", async () => {
    render(<TwinScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Previous recorded state" }));
    expect(setSelectedTwinSnapshot).toHaveBeenCalledWith("snap-old");
  });

  it("returns to the newest row from Today", async () => {
    storeState.selectedTwinSnapshotId = "snap-old";
    render(<TwinScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Today" }));
    expect(setSelectedTwinSnapshot).toHaveBeenCalledWith("snap-new");
  });
});

describe("header actions", () => {
  it("offers Check in to a signed-in athlete", async () => {
    render(<TwinScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Check in" }));
    expect(openCheckin).toHaveBeenCalledTimes(1);
  });

  it("does not offer Check in to a guest", () => {
    token = null;
    render(<TwinScreen />);
    expect(screen.queryByRole("button", { name: "Check in" })).toBeNull();
  });
});
