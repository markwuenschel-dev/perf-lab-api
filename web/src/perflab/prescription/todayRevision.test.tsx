// @vitest-environment jsdom
//
// P1b: every surface showing today's ACTIONABLE session reads one immutable revision
// (GET /v1/planning/today). Pinned here: the pure rules (todayRevision.ts), the notice +
// re-check control, and Planning's card — the issued revision when a session is planned,
// a labelled preview only when nothing is.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PrescriptionRevisionRead, TodaySessionResponse, WorkoutPrescription } from "@/types";
import { RevisionNotice } from "./RevisionNotice";
import { canRecheck, prefillFromToday, revisionNotice } from "./todayRevision";

const revision = (over: Partial<PrescriptionRevisionRead> = {}): PrescriptionRevisionRead => ({
  id: 7,
  revision_no: 1,
  reason: "first_issue",
  safety_kind: "none",
  issued_at: "2026-10-04T08:00:00Z",
  issued_now: false,
  ...over,
});

const RX = (focus: string): WorkoutPrescription =>
  ({
    type: "Strength",
    focus,
    rationale: "r",
    duration_min: 45,
    calculated_duration_min: null,
    duration_estimate: null,
    model_version: "v0.8",
    exercises: [{ name: "Back Squat", sets: 3, reps: "5", load_note: "RPE 7", weak_point_tags: [] }],
    why: null,
  }) as WorkoutPrescription;

// ── pure rules ────────────────────────────────────────────────────────────────────────

describe("the revision rules", () => {
  it("a first issue (or a re-issue of legacy content) replaced nothing, so it says nothing", () => {
    expect(revisionNotice(revision())).toBeNull();
    expect(revisionNotice(revision({ reason: "legacy_reissue" }))).toBeNull();
    expect(revisionNotice(null)).toBeNull();
  });

  it.each([
    ["issued_no_longer_safe", /no longer passes a safety check/],
    ["safety_outcome_changed", /safety restriction changed/],
    ["safety_check_rerun", /couldn't run earlier/],
    ["athlete_recheck", /restriction has cleared/],
  ])("a replacement for %s says why", (reason, text) => {
    expect(revisionNotice(revision({ revision_no: 2, reason }))).toMatch(text);
  });

  it("re-check is offered only while a safety restriction is in force", () => {
    expect(canRecheck(revision({ safety_kind: "none" }))).toBe(false);
    expect(canRecheck(null)).toBe(false);
    expect(canRecheck(revision({ safety_kind: "safety_override" }))).toBe(true);
    expect(canRecheck(revision({ safety_kind: "readiness_redirect" }))).toBe(true);
  });

  it("the Log pre-fill takes session, revision and exercises from ONE response", () => {
    const today = {
      session: { id: 41 },
      prescription: RX("Heavy Lower"),
      revision: revision({ id: 900 }),
    } as unknown as TodaySessionResponse;
    expect(prefillFromToday(today)).toEqual({
      plannedSessionId: 41,
      revisionId: 900,
      exercises: RX("Heavy Lower").exercises,
    });
    expect(prefillFromToday({ session: null, prescription: null } as TodaySessionResponse)).toBeNull();
    expect(prefillFromToday(null)).toBeNull();
  });
});

// ── the notice and its re-check ───────────────────────────────────────────────────────

afterEach(cleanup);

describe("RevisionNotice", () => {
  it("shows nothing for an ordinary, unreplaced session", () => {
    const { container } = render(<RevisionNotice revision={revision()} onRecheck={vi.fn()} />);
    expect(container.textContent).toBe("");
  });

  it("re-checks a restricted session on request", async () => {
    const onRecheck = vi.fn(() => Promise.resolve());
    render(
      <RevisionNotice
        revision={revision({ revision_no: 2, reason: "safety_outcome_changed", safety_kind: "safety_override" })}
        onRecheck={onRecheck}
      />,
    );
    expect(screen.getByText(/safety restriction changed/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Re-check today's session" }));
    await vi.waitFor(() => expect(onRecheck).toHaveBeenCalledTimes(1));
  });

  it("a failed re-check says so instead of failing silently", async () => {
    render(
      <RevisionNotice
        revision={revision({ safety_kind: "safety_override" })}
        onRecheck={() => Promise.reject(new Error("409"))}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Re-check today's session" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});

// ── Planning's card reads the issued revision ─────────────────────────────────────────

let todayResponse: TodaySessionResponse = { session: null, prescription: null } as TodaySessionResponse;
let nextSessionCalls = 0;
let recheckCalls = 0;

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token: "real-token", user: null, profile: null, email: null, isGuest: false }),
}));

vi.mock("../store", () => ({
  usePerfLab: () => ({
    state: {
      settings: { goal: "Strength", units: "Metric (km)" },
      planningWeekAnchor: null,
      planningRefreshKey: 0,
      feedbackRefreshKey: 0,
      readinessRefreshKey: 0,
    },
    actions: { setScreen: vi.fn(), openLog: vi.fn(), openBlockCreate: vi.fn(), openFeedback: vi.fn(), focusPlanningWeek: vi.fn() },
  }),
}));

vi.mock("@/api/perfLabClient", () => ({
  getTodayPlannedSession: () => Promise.resolve(todayResponse),
  getNextSession: () => {
    nextSessionCalls += 1;
    return Promise.resolve(RX("Preview focus"));
  },
  recheckTodayPlannedSession: () => {
    recheckCalls += 1;
    return Promise.resolve(todayResponse);
  },
  getReadiness: () => new Promise(() => {}),
  getStateHistory: () => new Promise(() => {}),
  listWorkouts: () => new Promise(() => {}),
  listPlanningBlocks: () => new Promise(() => {}),
  getPlannedWeekProjection: () => new Promise(() => {}),
  // The week has a session today, so Planning renders its authed body and the card.
  listPlannedSessions: () =>
    Promise.resolve([
      {
        id: 41, block_id: 1, user_id: 1, scheduled_date: todayIso(), original_scheduled_date: null,
        week_number: 1, day_of_week: 1, category: "Heavy Lower", modality: "Strength", status: "pending",
        is_deload: false, is_benchmark: false, benchmark_key: null, prescribed_content: null,
        workout_log_id: null, completed_at: null,
      },
    ]),
}));

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

describe("Planning's Prescribed session card", () => {
  beforeEach(() => {
    nextSessionCalls = 0;
    recheckCalls = 0;
    todayResponse = { session: null, prescription: null } as TodaySessionResponse;
  });

  it("shows today's issued revision and asks for no preview", async () => {
    todayResponse = {
      session: { id: 41 },
      prescription: RX("Issued focus"),
      revision: revision({ id: 900, revision_no: 2, reason: "safety_outcome_changed", safety_kind: "safety_override" }),
    } as unknown as TodaySessionResponse;
    const { PlanningScreen } = await import("../screens/PlanningScreen");
    render(<PlanningScreen />);
    expect(await screen.findByText("Issued focus")).toBeTruthy();
    expect(screen.getByText("Prescribed session")).toBeTruthy();
    expect(screen.getByText(/safety restriction changed/)).toBeTruthy();
    expect(nextSessionCalls).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Re-check today's session" }));
    await vi.waitFor(() => expect(recheckCalls).toBe(1));
  });

  it("labels a preview as one when nothing is planned today", async () => {
    const { PlanningScreen } = await import("../screens/PlanningScreen");
    render(<PlanningScreen />);
    expect(await screen.findByText("Preview focus")).toBeTruthy();
    expect(screen.getByText("Preview — nothing planned today")).toBeTruthy();
    expect(nextSessionCalls).toBe(1);
  });
});
