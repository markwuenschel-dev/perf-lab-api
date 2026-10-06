// @vitest-environment jsdom
//
// THE FEEDBACK HONESTY ORACLE.
//
// This overlay used to show every athlete invented distance/pace/HR and a "Twin
// updated" screen backed by nothing but local state. Two properties keep that
// from coming back, and neither can be established by reading the code:
//
//   - a signed-in athlete never sees fixture data or an unbacked success claim
//   - what the form sends is what the athlete actually reported
//
// The payload half is asserted against the pure builder so the mapping is pinned
// exactly; the render half is asserted through the component so the boundary
// holds regardless of who opens the overlay.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildFeedbackBody, FeedbackModal } from "./FeedbackModal";

let token: string | null = null;
let feedbackSessionId: number | null = null;
let feedbackSessionStatus: string | null = null;

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token, isGuest: token == null }),
}));

vi.mock("../store", () => ({
  usePerfLab: () => ({
    state: {
      feedbackOpen: true,
      feedbackApplied: false,
      feedbackSessionId,
      feedbackSessionStatus,
      feel: "controlled",
      rpe: null,
      sim: {},
    },
    actions: {
      closeFeedback: vi.fn(),
      applyFeedback: vi.fn(),
      feedbackToTwin: vi.fn(),
      refreshFeedback: vi.fn(),
      setFeel: vi.fn(),
      setRpe: vi.fn(),
    },
  }),
}));

vi.mock("../sim", () => ({
  COLORS: { hot: "#f00", teal: "#0ff" },
  projectLogDose: () => ({
    readyAfter: 58,
    fatAfter: 41,
    capDelta: "+0.4",
    cap: "Aerobic",
    readyColor: "#0f0",
  }),
}));

vi.mock("@/api/perfLabClient", () => ({ createSessionFeedback: vi.fn() }));

afterEach(() => {
  cleanup();
  token = null;
  feedbackSessionId = null;
  feedbackSessionStatus = null;
});

const FIXTURE_STRINGS = ["9.1 km", "53:20", "4:32", "168"];

describe("the authenticated athlete never sees the demo", () => {
  it("renders no fixture statistic when a real session is being reported on", () => {
    token = "tok";
    feedbackSessionId = 42;
    const { container } = render(<FeedbackModal />);
    for (const fixture of FIXTURE_STRINGS) {
      expect(container.textContent).not.toContain(fixture);
    }
  });

  it("never claims the twin updated — feedback is a label, not a dose", () => {
    token = "tok";
    feedbackSessionId = 42;
    const { container } = render(<FeedbackModal />);
    expect(container.textContent?.toLowerCase()).not.toContain("twin updated");
  });

  it("names the session it is reporting on, so it cannot be about some other one", () => {
    token = "tok";
    feedbackSessionId = 42;
    render(<FeedbackModal />);
    expect(screen.getByText(/Session #42/)).toBeTruthy();
  });

  it("shows nothing at all when signed in with no session id, rather than the preview", () => {
    token = "tok";
    feedbackSessionId = null;
    const { container } = render(<FeedbackModal />);
    expect(container.textContent).toBe("");
  });

  it("still gives a signed-out visitor the preview, clearly labelled as sample data", () => {
    token = null;
    feedbackSessionId = null;
    const { container } = render(<FeedbackModal />);
    expect(container.textContent).toContain("9.1 km");
    expect(container.textContent?.toLowerCase()).toContain("sample data");
  });
});

describe("the outcomes offered follow the session's status (P2b)", () => {
  const offered = () =>
    ["As prescribed", "Changed it", "Skipped"].filter((label) => screen.queryByText(label) != null);

  it.each([
    ["missed", ["Skipped"]],
    ["skipped", ["Skipped"]],
    ["completed", ["As prescribed", "Changed it"]],
    [null, ["As prescribed", "Changed it", "Skipped"]],
  ] as const)("%s offers %j", (status, labels) => {
    token = "tok";
    feedbackSessionId = 42;
    feedbackSessionStatus = status;
    render(<FeedbackModal />);
    expect(offered()).toEqual(labels);
  });

  it("a miss starts on Skipped and says how to report training that did happen", () => {
    token = "tok";
    feedbackSessionId = 42;
    feedbackSessionStatus = "missed";
    render(<FeedbackModal />);
    expect(screen.getByText("Why did you skip it?")).toBeTruthy();
    expect(screen.getByText(/If you trained, log the workout instead/)).toBeTruthy();
  });
});

describe("the request body says what the athlete said", () => {
  const base = {
    outcome: "completed" as const,
    modifiedVolume: false,
    modifiedIntensity: false,
    modifiedExercises: false,
    reason: "",
    satisfaction: null,
    pain: false,
    soreness: false,
    notes: "",
  };

  it("claims followed-as-prescribed only for a session reported as completed", () => {
    expect(buildFeedbackBody(1, base).followed_as_prescribed).toBe(true);
    expect(buildFeedbackBody(1, { ...base, outcome: "modified" }).followed_as_prescribed).toBe(false);
  });

  it("leaves followed-as-prescribed unstated for a skip", () => {
    // A session that never happened was neither followed nor not followed.
    expect(buildFeedbackBody(1, { ...base, outcome: "skipped" }).followed_as_prescribed).toBeNull();
  });

  it("sends no modification flag for a session reported as completed", () => {
    // Stale toggles from a changed mind must not leak: a completed session that
    // once had 'volume' ticked would otherwise be counted as friction.
    const body = buildFeedbackBody(1, { ...base, modifiedVolume: true, modifiedIntensity: true });
    expect(body.modified_volume).toBe(false);
    expect(body.modified_intensity).toBe(false);
  });

  it("carries the modification flags the athlete actually ticked", () => {
    const body = buildFeedbackBody(7, {
      ...base,
      outcome: "modified",
      modifiedVolume: true,
      modifiedExercises: true,
    });
    expect(body.planned_session_id).toBe(7);
    expect(body.status).toBe("modified");
    expect(body.modified_volume).toBe(true);
    expect(body.modified_exercises).toBe(true);
    expect(body.modified_intensity).toBe(false);
  });

  it("routes the free-text reason to the field matching the outcome", () => {
    const changed = buildFeedbackBody(1, { ...base, outcome: "modified", reason: "shoulder" });
    expect(changed.modification_reason).toBe("shoulder");
    expect(changed.skip_reason).toBeNull();

    const skipped = buildFeedbackBody(1, { ...base, outcome: "skipped", reason: "travel" });
    expect(skipped.skip_reason).toBe("travel");
    expect(skipped.modification_reason).toBeNull();
  });

  it("sends null rather than an empty string for untouched free text", () => {
    const body = buildFeedbackBody(1, { ...base, outcome: "skipped", reason: "   ", notes: "  " });
    expect(body.skip_reason).toBeNull();
    expect(body.notes).toBeNull();
  });

  it("leaves an unrated session unrated instead of defaulting a score", () => {
    expect(buildFeedbackBody(1, base).satisfaction_score).toBeNull();
    expect(buildFeedbackBody(1, { ...base, satisfaction: 4 }).satisfaction_score).toBe(4);
  });
});
