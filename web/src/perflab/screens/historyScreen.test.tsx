// @vitest-environment jsdom
//
// Behavioural guarantees of the History screen that only a render can establish:
//
//   - the range control marks exactly one window as pressed and switches it
//   - clicking a readiness point time-travels the Twin by that ROW's snapshot_id
//     (its durable identity), never by the chart index
//   - Check in is offered to a signed-in athlete, not a guest
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let token: string | null = "real-token";
const setSelectedTwinSnapshot = vi.fn();
const setScreen = vi.fn();

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token, user: null, profile: null, email: "", isGuest: token == null }),
}));

vi.mock("../store", () => ({
  usePerfLab: () => ({
    state: {},
    actions: { setSelectedTwinSnapshot, setScreen, openCheckin: vi.fn(), openLog: vi.fn() },
  }),
}));

const daysAgo = (d: number) => new Date(Date.now() - d * 864e5).toISOString();
const row = (id: string, d: number, fatigue: number) => ({
  snapshot_id: id,
  timestamp: daysAgo(d),
  fatigue_f: { cns: fatigue, muscular: fatigue, metabolic: fatigue, structural: fatigue, tendon: fatigue, grip: fatigue },
  capacity_x: { aerobic: 400 },
});
const HISTORY = [row("snap-a", 3, 40), row("snap-b", 2, 30), row("snap-c", 1, 20)];

vi.mock("@/api/perfLabClient", () => ({
  getStateHistory: () => Promise.resolve(HISTORY),
  listWorkouts: () => Promise.resolve([]),
  listBenchmarkObservations: () => Promise.resolve([]),
  listWellness: () => Promise.resolve([]),
}));

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const { HistoryScreen } = await import("./HistoryScreen");

beforeEach(() => {
  token = "real-token";
  setSelectedTwinSnapshot.mockClear();
  setScreen.mockClear();
});

afterEach(cleanup);

describe("range control", () => {
  it("presses exactly the selected window and switches on click", () => {
    render(<HistoryScreen />);
    const range = within(screen.getByRole("group", { name: "Range" }));
    const pressed = () => range.getAllByRole("button").filter((b) => b.getAttribute("aria-pressed") === "true").map((b) => b.textContent);
    expect(pressed()).toEqual(["12w"]);
    fireEvent.click(range.getByRole("button", { name: "4w" }));
    expect(pressed()).toEqual(["4w"]);
  });
});

describe("readiness points time-travel the Twin", () => {
  it("hands the Twin the clicked row's snapshot_id", async () => {
    const { container } = render(<HistoryScreen />);
    await screen.findByText("click a point to time-travel");
    // Wait for the chart: the hit targets are the transparent r=10 circles.
    await vi.waitFor(() => expect(container.querySelectorAll("circle.cursor-pointer").length).toBe(HISTORY.length));
    fireEvent.click(container.querySelectorAll("circle.cursor-pointer")[1]);
    expect(setSelectedTwinSnapshot).toHaveBeenCalledWith("snap-b");
    expect(setScreen).toHaveBeenCalledWith("twin");
  });
});

describe("header actions", () => {
  it("offers Check in to a signed-in athlete only", () => {
    render(<HistoryScreen />);
    expect(screen.getByRole("button", { name: "Check in" })).toBeTruthy();
    cleanup();
    token = null;
    render(<HistoryScreen />);
    expect(screen.queryByRole("button", { name: "Check in" })).toBeNull();
  });
});
