// @vitest-environment jsdom
//
// Behavioural guarantees of the Simulator controls that only a render can
// establish:
//
//   - a quick scenario shows as active only while ALL THREE values it sets
//     (volume, intensity, recovery) still match — and it reads them from the
//     same SIM_PRESETS table the action applies
//   - every training goal stays selectable as a chip (not just the mock's six)
//   - Check in is offered to a signed-in athlete, not a guest
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let token: string | null = null;
const simPreset = vi.fn();
const setSim = vi.fn();
const openCheckin = vi.fn();

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token, user: null, profile: null, email: "", isGuest: token == null }),
}));

const sim = { volume: 62, intensity: "balanced", recovery: "standard", weeks: 8, goal: "Strength" };

vi.mock("../store", async () => {
  const real = await vi.importActual<typeof import("../store")>("../store");
  return {
    SIM_PRESETS: real.SIM_PRESETS,
    TRAINING_GOALS: real.TRAINING_GOALS,
    usePerfLab: () => ({ state: { sim }, actions: { simPreset, setSim, openCheckin, openLog: vi.fn() } }),
  };
});

vi.mock("@/api/perfLabClient", () => ({
  getSimulateProjection: () => new Promise(() => {}),
}));

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const { SimulatorScreen } = await import("./SimulatorScreen");
const { SIM_PRESETS, TRAINING_GOALS } = await import("../store");

const pressed = (name: string) => screen.getByRole("button", { name }).getAttribute("aria-pressed");

beforeEach(() => {
  token = null;
  Object.assign(sim, SIM_PRESETS.build);
  simPreset.mockClear();
  setSim.mockClear();
});

afterEach(cleanup);

describe("quick scenarios", () => {
  it("marks the preset whose three values all match", () => {
    render(<SimulatorScreen />);
    expect(pressed("build")).toBe("true");
    expect(pressed("maintain")).toBe("false");
    expect(pressed("aggressive")).toBe("false");
  });

  it("marks none once any one of the three drifts", () => {
    sim.volume = SIM_PRESETS.build.volume + 2;
    render(<SimulatorScreen />);
    for (const p of ["maintain", "build", "aggressive"]) expect(pressed(p)).toBe("false");
  });

  it("applies a preset by name", () => {
    render(<SimulatorScreen />);
    fireEvent.click(screen.getByRole("button", { name: "aggressive" }));
    expect(simPreset).toHaveBeenCalledWith("aggressive");
  });
});

describe("goal chips", () => {
  it("offers every training goal and marks the current one", () => {
    render(<SimulatorScreen />);
    // Scoped: "Power" and "Hypertrophy" are also trajectory axis chips.
    const goals = within(screen.getByRole("group", { name: "Goal" }));
    expect(goals.getAllByRole("button")).toHaveLength(TRAINING_GOALS.length);
    for (const g of TRAINING_GOALS) expect(goals.getByRole("button", { name: g.label })).toBeTruthy();
    expect(goals.getByRole("button", { name: "Strength" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(goals.getByRole("button", { name: "Powerlifting" }));
    expect(setSim).toHaveBeenCalledWith({ goal: "Powerlifting" });
  });
});

describe("header actions", () => {
  it("hides Check in from a guest", () => {
    render(<SimulatorScreen />);
    expect(screen.queryByRole("button", { name: "Check in" })).toBeNull();
  });

  it("offers Check in to a signed-in athlete", () => {
    token = "real-token";
    render(<SimulatorScreen />);
    expect(screen.getByRole("button", { name: "Check in" })).toBeTruthy();
  });
});
