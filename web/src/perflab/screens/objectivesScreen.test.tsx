// @vitest-environment jsdom
//
// Behavioural guarantees of the Objectives program timeline that only a render can
// establish:
//
//   - reordering writes DISPLAY order only: the full active id list in the new order via
//     setObjectiveOrder, optimistically — and never a priority change (no updateObjective)
//   - a failed order write rolls the list back and refetches
//   - PRIMARY marks the objective /objectives/driving names, not whatever is at rank 1
//   - the block strip's widths are ∝ weeks, carry the API phase, and end in the
//     unplanned remainder (no invented future blocks); NOW sits inside the current block
//   - the linked-benchmark evidence chip is joined from the assessment surface by code
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let token: string | null = "real-token";
const refreshObjectives = vi.fn();
const openCheckin = vi.fn();

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token, user: { email: "athlete@example.com" }, profile: null, email: "athlete@example.com", isGuest: token == null }),
}));

vi.mock("../store", () => ({
  usePerfLab: () => ({
    state: { objectivesRefreshKey: 0, macrocyclesRefreshKey: 0 },
    actions: {
      refreshObjectives,
      openCheckin,
      openLog: vi.fn(),
      openAuth: vi.fn(),
      openObjectiveCreate: vi.fn(),
      openMacrocycleCreate: vi.fn(),
    },
  }),
}));

const objective = (id: number, label: string, priority: number, display_rank: number | null, extra: Record<string, unknown> = {}) => ({
  id,
  user_id: 1,
  label,
  priority,
  display_rank,
  status: "active",
  domain: "strength",
  benchmark_code: null,
  target_value: null,
  target_unit: null,
  target_date: null,
  days_to_go: null,
  created_at: "2026-09-01T00:00:00Z",
  progress: { pct: null, current: null, target: null, direction: null },
  ...extra,
});

const HYROX = "Hyrox Doubles";
const SQUAT = "Back squat 150 kg";
const RUN = "5 km under 21:00";

const OBJECTIVES = [
  objective(11, HYROX, 1, 1, { domain: "mixed", target_date: "2026-11-19", days_to_go: 62 }),
  objective(12, SQUAT, 2, 2, { benchmark_code: "squat_1rm", progress: { pct: 95, current: 142, target: 150, direction: "higher" } }),
  objective(13, RUN, 3, 3, { domain: "running", benchmark_code: "run_5k" }),
  objective(14, "Old deadlift PR", 4, null, { status: "achieved" }),
];

function iso(daysFromToday: number): string {
  const d = new Date();
  const u = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate() + daysFromToday));
  return u.toISOString().slice(0, 10);
}

const block = (id: number, goal: string, start: number, weeks: number, phase: string, extra: Record<string, unknown> = {}) => ({
  id,
  goal,
  start_date: iso(start),
  end_date: iso(start + weeks * 7 - 1),
  duration_weeks: weeks,
  status: phase === "completed" ? "completed" : "active",
  phase,
  deload_weeks: [],
  benchmark_weeks: [],
  block_taper_week: null,
  ...extra,
});

const MACRO = {
  id: 5,
  user_id: 1,
  objective_id: 11,
  objective_label: HYROX,
  start_date: iso(-42),
  status: "active",
  target_date: "2026-11-19",
  block_count: 2,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  week_progress: { current_week: 7, total_weeks: 15, pct: 40, weeks_to_go: 8 },
  // 4 wk completed, 6 wk current (today is day 14 → block week 3), 5 unplanned weeks.
  blocks: [
    block(1, "Strength", -42, 4, "completed", { benchmark_weeks: [4] }),
    block(2, "Hyrox", -14, 6, "current", { block_taper_week: 6 }),
  ],
  unplanned_weeks: 5,
};

const SURFACE = {
  active_domains: ["strength", "running"],
  mode: "onramp",
  policy_version: "v1",
  recommended: [],
  groups: [
    { domain: "strength", cards: [{ code: "squat_1rm", confidence_status: "established" }] },
    { domain: "running", cards: [{ code: "run_5k", confidence_status: "provisional" }] },
  ],
};

let driving: { objective_id: number | null; source: string | null } = { objective_id: 12, source: "macrocycle_anchor" };
const setObjectiveOrder = vi.fn();
const updateObjective = vi.fn();

vi.mock("@/api/perfLabClient", () => ({
  listObjectives: () => Promise.resolve(OBJECTIVES),
  listMacrocycles: () => Promise.resolve([MACRO]),
  getDrivingObjective: () => Promise.resolve(driving),
  getAssessmentSurface: () => Promise.resolve(SURFACE),
  setObjectiveOrder: (...args: unknown[]) => setObjectiveOrder(...args),
  updateObjective: (...args: unknown[]) => updateObjective(...args),
  deleteObjective: () => Promise.resolve(),
}));

// The viz layer sizes itself with ResizeObserver, which jsdom does not provide.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const { ObjectivesScreen } = await import("./ObjectivesScreen");

/** The active objectives' labels, top to bottom, as rendered. */
function renderedOrder(): string[] {
  return screen.getAllByTestId("objective-row").map((row) => {
    const hit = [HYROX, SQUAT, RUN].find((l) => within(row).queryByText(l) != null);
    return hit ?? "?";
  });
}

function rowFor(label: string): HTMLElement {
  const row = screen.getAllByTestId("objective-row").find((r) => within(r).queryByText(label) != null);
  if (!row) throw new Error(`no row for ${label}`);
  return row;
}

beforeEach(() => {
  token = "real-token";
  driving = { objective_id: 12, source: "macrocycle_anchor" };
  setObjectiveOrder.mockReset();
  updateObjective.mockReset();
  refreshObjectives.mockClear();
  openCheckin.mockClear();
});

afterEach(cleanup);

describe("reorder writes display order only", () => {
  it("sends the full active id list in the new order, optimistically, and never a priority change", async () => {
    let resolve!: (v: unknown) => void;
    setObjectiveOrder.mockImplementation(() => new Promise((r) => (resolve = r)));
    render(<ObjectivesScreen />);
    fireEvent.click(await screen.findByRole("button", { name: `Move ${RUN} up` }));

    // Optimistic: the new order is on screen before the server answers.
    expect(renderedOrder()).toEqual([HYROX, RUN, SQUAT]);
    expect(setObjectiveOrder).toHaveBeenCalledTimes(1);
    expect(setObjectiveOrder).toHaveBeenCalledWith([11, 13, 12], "real-token");
    expect(updateObjective).not.toHaveBeenCalled();

    resolve([]);
    await waitFor(() => expect(refreshObjectives).toHaveBeenCalledTimes(1));
    expect(renderedOrder()).toEqual([HYROX, RUN, SQUAT]);
    expect(updateObjective).not.toHaveBeenCalled();
  });

  it("reorders by drag and drop to the same full-list write", async () => {
    setObjectiveOrder.mockResolvedValue([]);
    render(<ObjectivesScreen />);
    await screen.findByText(RUN);
    const dataTransfer = { setData: vi.fn(), effectAllowed: "", dropEffect: "" };
    fireEvent.dragStart(rowFor(HYROX), { dataTransfer });
    fireEvent.dragOver(rowFor(RUN), { dataTransfer });
    fireEvent.drop(rowFor(RUN), { dataTransfer });
    expect(setObjectiveOrder).toHaveBeenCalledWith([12, 13, 11], "real-token");
    expect(renderedOrder()).toEqual([SQUAT, RUN, HYROX]);
    expect(updateObjective).not.toHaveBeenCalled();
  });

  it("does not offer moves past either end", async () => {
    render(<ObjectivesScreen />);
    expect((await screen.findByRole("button", { name: `Move ${HYROX} up` })).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: `Move ${RUN} down` }).hasAttribute("disabled")).toBe(true);
  });

  it("rolls back and refetches when the order write fails", async () => {
    setObjectiveOrder.mockRejectedValue({ message: "objective_ids must list every active objective" });
    render(<ObjectivesScreen />);
    fireEvent.click(await screen.findByRole("button", { name: `Move ${SQUAT} up` }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(renderedOrder()).toEqual([HYROX, SQUAT, RUN]);
    expect(refreshObjectives).toHaveBeenCalledTimes(1);
    expect(updateObjective).not.toHaveBeenCalled();
  });
});

describe("PRIMARY follows /objectives/driving", () => {
  it("badges the driving objective even when it is not at rank 1", async () => {
    render(<ObjectivesScreen />);
    await waitFor(() => expect(screen.getAllByTestId("primary-badge")).toHaveLength(1));
    expect(within(rowFor(SQUAT)).queryByTestId("primary-badge")).not.toBeNull();
    expect(within(rowFor(HYROX)).queryByTestId("primary-badge")).toBeNull();
  });

  it("does not move when the list is reordered", async () => {
    setObjectiveOrder.mockResolvedValue([]);
    render(<ObjectivesScreen />);
    await waitFor(() => expect(screen.getAllByTestId("primary-badge")).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: `Move ${RUN} up` }));
    await waitFor(() => expect(screen.getByRole("button", { name: `Move ${RUN} up` }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: `Move ${RUN} up` }));
    await waitFor(() => expect(renderedOrder()[0]).toBe(RUN));
    expect(setObjectiveOrder).toHaveBeenLastCalledWith([13, 11, 12], "real-token");
    expect(within(rowFor(RUN)).queryByTestId("primary-badge")).toBeNull();
    expect(within(rowFor(SQUAT)).queryByTestId("primary-badge")).not.toBeNull();
  });

  it("shows no badge when nothing drives training", async () => {
    driving = { objective_id: null, source: null };
    render(<ObjectivesScreen />);
    await screen.findByText(RUN);
    await waitFor(() => expect(screen.queryAllByTestId("evidence-chip").length).toBeGreaterThan(0));
    expect(screen.queryByTestId("primary-badge")).toBeNull();
  });
});

describe("program timeline", () => {
  it("sizes block widths by weeks, carries phases, and ends in the unplanned remainder", async () => {
    render(<ObjectivesScreen />);
    await screen.findByText(`Program · anchored to ${HYROX}`);
    const segs = screen.getAllByTestId("timeline-segment");
    expect(segs.map((s) => [s.dataset.phase, s.style.flex])).toEqual([
      ["completed", "4 4 0%"],
      ["current", "6 6 0%"],
      ["unplanned", "5 5 0%"],
    ]);
    expect(screen.getByText("wk 3 of 6 · now")).toBeTruthy();
    expect(screen.getByText("5 wk · not generated yet")).toBeTruthy();
    // Block-local taper is the current block's final week; benchmark week 4 of the first block.
    expect(within(segs[1]).getByTestId("taper-week").style.left).toBe(`${(5 / 6) * 100}%`);
    expect(within(segs[0]).getByTestId("benchmark-week").style.left).toBe("75%");
    // NOW: 4 completed weeks + 2 weeks into the current block, of 15.
    const pin = screen.getByTestId("now-pin");
    expect(pin.textContent).toBe("NOW · wk 7");
    expect(pin.style.left).toBe("40%");
  });
});

describe("linked-benchmark evidence", () => {
  it("joins confidence_status from the assessment surface by benchmark code", async () => {
    render(<ObjectivesScreen />);
    await waitFor(() => expect(screen.getAllByTestId("evidence-chip")).toHaveLength(2));
    const squat = within(rowFor(SQUAT)).getByTestId("evidence-chip");
    const run = within(rowFor(RUN)).getByTestId("evidence-chip");
    expect(squat.textContent).toBe("established");
    expect(squat.className).toContain("text-good");
    expect(run.textContent).toBe("provisional");
    expect(run.className).toContain("text-warn");
    expect(within(rowFor(HYROX)).queryByTestId("evidence-chip")).toBeNull();
  });
});

describe("hidden and header rows", () => {
  it("never renders a per-objective 'this week' line", async () => {
    render(<ObjectivesScreen />);
    await screen.findByText(RUN);
    expect(screen.queryByText(/this week/i)).toBeNull();
  });

  it("offers Check in to a signed-in athlete", async () => {
    render(<ObjectivesScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Check in" }));
    expect(openCheckin).toHaveBeenCalledTimes(1);
  });
});
