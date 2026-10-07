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
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

const createSessionFeedback = vi.fn();
vi.mock("@/api/perfLabClient", () => ({ createSessionFeedback: (...a: unknown[]) => createSessionFeedback(...a) }));

afterEach(() => {
  cleanup();
  createSessionFeedback.mockReset();
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

  it("a miss starts on Skipped, promises nothing about logging, and claims no prescription effect", () => {
    token = "tok";
    feedbackSessionId = 42;
    feedbackSessionStatus = "missed";
    const { container } = render(<FeedbackModal />);
    expect(screen.getByText("Why did you skip it?")).toBeTruthy();
    // The logger cannot target a past session, so the form must not promise that logging does.
    expect(container.textContent).not.toMatch(/log the workout/i);
    // Skipped feedback on a miss feeds no adherence input (backend policy), so no "bias" claim.
    expect(container.textContent).not.toMatch(/bias/i);
    expect(screen.getByText("Kept with this missed session. It doesn't change your prescription.")).toBeTruthy();
  });

  it("the success screen for a miss says the session stays missed and nothing else changed", async () => {
    token = "tok";
    feedbackSessionId = 42;
    feedbackSessionStatus = "missed";
    createSessionFeedback.mockResolvedValueOnce({ id: 1 } as never);
    const { container } = render(<FeedbackModal />);
    fireEvent.click(screen.getByRole("button", { name: "Record feedback →" }));
    expect(await screen.findByText("Feedback recorded")).toBeTruthy();
    expect(container.textContent).toMatch(/The session stays missed/);
    expect(container.textContent).not.toMatch(/bias/i);
  });

  it("switching to another session starts a fresh draft and submits only what is shown", async () => {
    token = "tok";
    feedbackSessionId = 41;
    feedbackSessionStatus = "completed";
    const view = render(<FeedbackModal />);
    fireEvent.click(screen.getByText("Changed it"));
    fireEvent.change((() => { const all = screen.getAllByPlaceholderText("Optional"); return all[all.length - 1]; })(), { target: { value: "for 41" } });

    // Review repro: the background Feedback button for a miss re-targets the open modal.
    feedbackSessionId = 43;
    feedbackSessionStatus = "missed";
    view.rerender(<FeedbackModal />);
    expect(screen.queryByText("Changed it")).toBeNull();
    createSessionFeedback.mockResolvedValueOnce({ id: 2 } as never);
    fireEvent.click(screen.getByRole("button", { name: "Record feedback →" }));
    await screen.findByText("Feedback recorded");
    const body = createSessionFeedback.mock.calls[createSessionFeedback.mock.calls.length - 1][0] as { planned_session_id: number; status: string; notes: string | null };
    expect([body.planned_session_id, body.status, body.notes]).toEqual([43, "skipped", null]);
  });

  it("keeps keyboard focus inside the dialog", () => {
    token = "tok";
    feedbackSessionId = 42;
    feedbackSessionStatus = "missed";
    render(
      <>
        <button>Feedback for another session</button>
        <FeedbackModal />
      </>,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
    // jsdom does not move focus on Tab by itself, so each assertion names exactly where the
    // trap must have put it: Tab off the last control wraps to the first, and back.
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>("button:not([disabled]), input"));
    const first = focusable[0];
    const record = screen.getByRole("button", { name: "Record feedback →" });
    expect(focusable[focusable.length - 1]).toBe(record);
    record.focus();
    fireEvent.keyDown(record, { key: "Tab" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(record);
  });
});

describe("focus cannot escape the dialog, during or after saving (P2b review)", () => {
  const open = () => {
    token = "tok";
    feedbackSessionId = 42;
    feedbackSessionStatus = "missed";
    render(
      <>
        <button>Background control</button>
        <FeedbackModal />
      </>,
    );
    return { dialog: screen.getByRole("dialog"), record: screen.getByRole("button", { name: "Record feedback →" }) };
  };

  it("the focused Record button stays focused while saving, and a second press does nothing", async () => {
    let resolve: (v: unknown) => void = () => {};
    createSessionFeedback.mockReturnValueOnce(new Promise((r) => { resolve = r; }));
    const { record } = open();
    record.focus();
    fireEvent.click(record);
    const saving = await screen.findByRole("button", { name: "Saving…" });
    expect(saving).toBe(record);
    expect(document.activeElement).toBe(record);
    expect([record.hasAttribute("disabled"), record.getAttribute("aria-disabled")]).toEqual([false, "true"]);
    fireEvent.click(record);
    expect(createSessionFeedback).toHaveBeenCalledTimes(1);
    resolve({ id: 9 });
    await screen.findByText("Feedback recorded");
  });

  it("a successful save puts focus on Done, not <body>", async () => {
    createSessionFeedback.mockResolvedValueOnce({ id: 9 } as never);
    const { record } = open();
    record.focus();
    fireEvent.click(record);
    await screen.findByText("Feedback recorded");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Done" }));
  });

  it("a Tab pressed while focus has fallen to <body> lands inside the dialog", () => {
    const { dialog } = open();
    (document.activeElement as HTMLElement).blur();
    expect(document.activeElement).toBe(document.body);
    fireEvent.keyDown(document.body, { key: "Tab" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(dialog);
  });

  it("focus that arrives on a background control is pulled back", () => {
    const { dialog } = open();
    screen.getByRole("button", { name: "Background control" }).focus();
    expect(dialog.contains(document.activeElement)).toBe(true);
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
