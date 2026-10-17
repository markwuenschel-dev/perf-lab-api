// @vitest-environment jsdom
//
// P8 fork 7 (D): the Overview hero shows the objective the SERVER resolved as driving
// (GET /v1/objectives/driving), looked up in the active list. The client never sorts or
// picks, and loading, failure or a disagreement between the two reads never reads as
// "you have no objectives".
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DrivingObjectiveRead, ObjectiveRead } from "@/types";
import { heroModel } from "../../objectives";

let token: string | null = "real-token";
const setScreen = vi.fn();

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token, user: null, profile: null, email: "athlete@example.com", isGuest: token == null }),
}));

const storeState = { objectivesRefreshKey: 0, macrocyclesRefreshKey: 0 };
vi.mock("../../store", () => ({
  usePerfLab: () => ({ state: storeState, actions: { setScreen } }),
  DEFAULT_GOAL: "endurance",
}));

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void };
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Each call to an endpoint takes the next queued answer (a value, an Error, or a Deferred). */
type Answer<T> = T | Error | Deferred<T>;
const listQueue: Answer<ObjectiveRead[]>[] = [];
const drivingQueue: Answer<DrivingObjectiveRead>[] = [];
let listCalls = 0;
let drivingCalls = 0;

function serve<T>(queue: Answer<T>[]): Promise<T> {
  const next = queue.length > 1 ? queue.shift()! : queue[0];
  if (next instanceof Error) return Promise.reject(next);
  if (next && typeof next === "object" && "promise" in next) return (next as Deferred<T>).promise;
  return Promise.resolve(next as T);
}

vi.mock("@/api/perfLabClient", () => ({
  listObjectives: () => {
    listCalls += 1;
    return serve(listQueue);
  },
  getDrivingObjective: () => {
    drivingCalls += 1;
    return serve(drivingQueue);
  },
}));

function obj(id: number, label: string, over: Partial<ObjectiveRead> = {}): ObjectiveRead {
  return {
    id,
    label,
    user_id: 1,
    status: "active",
    priority: 1,
    display_rank: null,
    domain: null,
    benchmark_code: null,
    target_date: null,
    target_value: null,
    target_unit: null,
    days_to_go: null,
    created_at: "2026-10-01T00:00:00Z",
    progress: { pct: null },
    ...over,
  };
}

const A = obj(1, "Objective A", { priority: 1, display_rank: 2 });
const B = obj(2, "Objective B", { priority: 2, display_rank: 1 });
const C = obj(3, "Objective C", { priority: 3, display_rank: 3 });

async function renderHero() {
  const { GoalObjectiveCard } = await import("./AuthedOverview");
  return render(<GoalObjectiveCard />);
}

const SET_GOAL = /Set a goal to aim at/;

beforeEach(() => {
  token = "real-token";
  listQueue.length = 0;
  drivingQueue.length = 0;
  listCalls = 0;
  drivingCalls = 0;
  storeState.objectivesRefreshKey = 0;
  storeState.macrocyclesRefreshKey = 0;
  setScreen.mockClear();
});

afterEach(cleanup);

describe("heroModel (pure)", () => {
  it("never substitutes a list objective for a missing driving id", () => {
    expect(heroModel([A, B], { objective_id: 99, source: "macrocycle_anchor" })).toEqual({
      kind: "missing",
      source: "macrocycle_anchor",
    });
  });
  it("reserves `none` for an empty list with no driving objective", () => {
    expect(heroModel([], { objective_id: null, source: null })).toEqual({ kind: "none" });
    expect(heroModel([A], { objective_id: null, source: null })).toEqual({ kind: "disagree" });
  });
});

describe("the hero shows the server-resolved driving objective", () => {
  it("A/B/C: an anchored program's objective wins over priority and display order", async () => {
    listQueue.push([B, A, C]); // display order: B first
    drivingQueue.push({ objective_id: C.id, source: "macrocycle_anchor" });
    await renderHero();
    expect(await screen.findByText("Objective C")).toBeTruthy();
    expect(screen.getByText(/Program objective/)).toBeTruthy();
    expect(screen.queryByText("Objective A")).toBeNull();
    expect(screen.queryByText("Objective B")).toBeNull();
  });

  it("with no program, reordering B to the top leaves the highest-priority A as driving", async () => {
    listQueue.push([B, A]);
    drivingQueue.push({ objective_id: A.id, source: "priority" });
    await renderHero();
    expect(await screen.findByText("Objective A")).toBeTruthy();
    expect(screen.getByText(/Highest-priority objective/)).toBeTruthy();
    expect(screen.queryByText("Objective B")).toBeNull();
  });

  it("a priority tie shows the server's pick, not the client's nearest date", async () => {
    const early = obj(10, "Earlier created", { priority: 1, days_to_go: 90 });
    const near = obj(11, "Nearer date", { priority: 1, days_to_go: 5 });
    listQueue.push([near, early]);
    drivingQueue.push({ objective_id: early.id, source: "priority" }); // lowest id wins on the server
    await renderHero();
    expect(await screen.findByText("Earlier created")).toBeTruthy();
    expect(screen.queryByText("Nearer date")).toBeNull();
  });

  it("an anchor missing from the active list reads as unavailable, with no list label", async () => {
    listQueue.push([A, B]);
    drivingQueue.push({ objective_id: 99, source: "macrocycle_anchor" });
    await renderHero();
    expect(await screen.findByText("Program objective details unavailable")).toBeTruthy();
    expect(screen.queryByText("Objective A")).toBeNull();
    expect(screen.queryByText("Objective B")).toBeNull();
    expect(screen.queryByText(SET_GOAL)).toBeNull();
    fireEvent.click(screen.getByText("Review objectives →"));
    expect(setScreen).toHaveBeenCalledWith("objectives");
  });

  it("a priority-source id missing from the list reads as unavailable too", async () => {
    listQueue.push([A]);
    drivingQueue.push({ objective_id: 42, source: "priority" });
    await renderHero();
    expect(await screen.findByText("Objective details unavailable")).toBeTruthy();
    expect(screen.queryByText("Objective A")).toBeNull();
  });

  it("an empty list with no driving objective invites a goal", async () => {
    listQueue.push([]);
    drivingQueue.push({ objective_id: null, source: null });
    await renderHero();
    expect(await screen.findByText(SET_GOAL)).toBeTruthy();
  });

  it("no driving objective but active objectives listed: unavailable + Retry, never a goal CTA", async () => {
    listQueue.push([A]);
    drivingQueue.push({ objective_id: null, source: null });
    await renderHero();
    expect(await screen.findByText("Objective details unavailable")).toBeTruthy();
    expect(screen.getByText("Retry")).toBeTruthy();
    expect(screen.queryByText(SET_GOAL)).toBeNull();
    expect(screen.queryByText("Objective A")).toBeNull();
  });
});

describe("resource states stay honest", () => {
  it("loading reads as loading", async () => {
    listQueue.push(deferred<ObjectiveRead[]>());
    drivingQueue.push(deferred<DrivingObjectiveRead>());
    await renderHero();
    expect(screen.getByText("Loading your objective…")).toBeTruthy();
    expect(screen.queryByText(SET_GOAL)).toBeNull();
  });

  it.each([
    ["the list", () => (listQueue.push(new Error("list down")), drivingQueue.push({ objective_id: null, source: null }))],
    ["the driving read", () => (listQueue.push([]), drivingQueue.push(new Error("driving down")))],
  ])("a failure of %s is an error with Retry, never a goal CTA", async (_name, arrange) => {
    arrange();
    await renderHero();
    expect(await screen.findByText("Couldn't load your objective")).toBeTruthy();
    expect(screen.queryByText(SET_GOAL)).toBeNull();
    // Retry re-reads both endpoints.
    const [l0, d0] = [listCalls, drivingCalls];
    listQueue.length = 0;
    drivingQueue.length = 0;
    listQueue.push([A]);
    drivingQueue.push({ objective_id: A.id, source: "priority" });
    fireEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText("Objective A")).toBeTruthy();
    expect(listCalls).toBe(l0 + 1);
    expect(drivingCalls).toBe(d0 + 1);
  });

  it.each(["list-first", "driving-first"])("%s arrival renders the same, with no intermediate objective", async (order) => {
    const list = deferred<ObjectiveRead[]>();
    const driving = deferred<DrivingObjectiveRead>();
    listQueue.push(list);
    drivingQueue.push(driving);
    await renderHero();
    if (order === "list-first") {
      await act(async () => list.resolve([B, A]));
    } else {
      await act(async () => driving.resolve({ objective_id: A.id, source: "priority" }));
    }
    // Half an answer shows nothing about any objective.
    expect(screen.getByText("Loading your objective…")).toBeTruthy();
    expect(screen.queryByText("Objective A")).toBeNull();
    expect(screen.queryByText("Objective B")).toBeNull();
    await act(async () => {
      if (order === "list-first") driving.resolve({ objective_id: A.id, source: "priority" });
      else list.resolve([B, A]);
    });
    expect(await screen.findByText("Objective A")).toBeTruthy();
    expect(screen.getByText(/Highest-priority objective/)).toBeTruthy();
    expect(screen.queryByText("Objective B")).toBeNull();
  });

  it("a refresh failure over a shown objective keeps it, with a stale notice and Retry", async () => {
    listQueue.push([A], new Error("refresh down"));
    drivingQueue.push({ objective_id: A.id, source: "priority" }, new Error("refresh down"));
    const view = await renderHero();
    expect(await screen.findByText("Objective A")).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
    storeState.objectivesRefreshKey = 1;
    const { GoalObjectiveCard } = await import("./AuthedOverview");
    view.rerender(<GoalObjectiveCard />);
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/Couldn't refresh/);
    expect(screen.getByText("Objective A")).toBeTruthy();
    expect(screen.getByText("Retry")).toBeTruthy();
  });

  it("a refresh failure over an empty result shows the failure, not the goal CTA", async () => {
    listQueue.push([], new Error("refresh down"));
    drivingQueue.push({ objective_id: null, source: null }, new Error("refresh down"));
    const view = await renderHero();
    expect(await screen.findByText(SET_GOAL)).toBeTruthy();
    storeState.objectivesRefreshKey = 1;
    const { GoalObjectiveCard } = await import("./AuthedOverview");
    view.rerender(<GoalObjectiveCard />);
    expect(await screen.findByText("Couldn't refresh your objective")).toBeTruthy();
    expect(screen.queryByText(SET_GOAL)).toBeNull();
    expect(screen.getByText("Retry")).toBeTruthy();
  });

  it.each([
    ["an anchor missing from the list", { objective_id: 999, source: "macrocycle_anchor" as const }, "Program objective details unavailable"],
    ["no driving objective over a non-empty list", { objective_id: null, source: null }, "Objective details unavailable"],
  ])("a refresh failure over %s says it is stale, keeps Retry, and still substitutes nothing", async (_label, driving, headline) => {
    listQueue.push([A, B], new Error("refresh down"));
    drivingQueue.push(driving, new Error("refresh down"));
    const view = await renderHero();
    expect(await screen.findByText(headline)).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
    storeState.objectivesRefreshKey = 1;
    const { GoalObjectiveCard } = await import("./AuthedOverview");
    view.rerender(<GoalObjectiveCard />);
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/Couldn't refresh/);
    expect(screen.getByText(headline)).toBeTruthy();
    expect(screen.getByText("Retry")).toBeTruthy();
    expect(screen.queryByText("Objective A")).toBeNull();
    expect(screen.queryByText("Objective B")).toBeNull();
  });

  it.each(["objectivesRefreshKey", "macrocyclesRefreshKey"] as const)("bumping %s re-reads both endpoints", async (key) => {
    listQueue.push([A]);
    drivingQueue.push({ objective_id: A.id, source: "priority" });
    const view = await renderHero();
    expect(await screen.findByText("Objective A")).toBeTruthy();
    const [l0, d0] = [listCalls, drivingCalls];
    storeState[key] = 1;
    const { GoalObjectiveCard } = await import("./AuthedOverview");
    view.rerender(<GoalObjectiveCard />);
    await vi.waitFor(() => expect(listCalls).toBe(l0 + 1));
    expect(drivingCalls).toBe(d0 + 1);
  });
});
