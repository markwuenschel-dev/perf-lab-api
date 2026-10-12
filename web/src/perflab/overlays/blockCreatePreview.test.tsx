// @vitest-environment jsdom
//
// src/perflab/overlays/blockCreatePreview.test.tsx
//
// The block-create preview labels each slot with the weekday it falls on (P5b, ADR-0074). Slots
// are block-relative, so the labels depend on the start date: changing it fetches a new preview.
// Against a server from before P5b (no `resolved_weekday`), slots read "Day N", never a weekday.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BlockCreateRequest, WeeklyTemplateSlot } from "@/types";
import { BlockCreateModal } from "./BlockCreateModal";

const TOKEN = "athlete-token";
const previewPlanningBlock = vi.fn();

vi.mock("@/api/perfLabClient", () => ({
  previewPlanningBlock: (...args: unknown[]) => previewPlanningBlock(...args),
  createPlanningBlock: vi.fn(),
}));

vi.mock("@/auth/useAuth", () => ({ useAuth: () => ({ token: TOKEN, profile: null }) }));

vi.mock("../store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../store")>();
  return {
    ...actual,
    usePerfLab: () => ({
      state: { blockCreateOpen: true },
      actions: { closeBlockCreate: vi.fn(), openAuth: vi.fn(), focusPlanningWeek: vi.fn() },
    }),
  };
});

/** General's authored week: block days 1, 3 and 5. */
const WEEK: WeeklyTemplateSlot[] = [
  { day_of_week: 1, category: "Full Body Strength", modality: "Strength", domain: "strength" },
  { day_of_week: 3, category: "Conditioning", modality: "Conditioning", domain: "conditioning" },
  { day_of_week: 5, category: "Full Body Strength", modality: "Strength", domain: "strength" },
];

/** What a P5b server answers: the weekday of start_date + (day_of_week - 1). */
function resolvedFor(body: BlockCreateRequest) {
  const start = new Date(`${body.start_date}T00:00:00`);
  return WEEK.map((slot) => {
    const d = new Date(start);
    d.setDate(start.getDate() + slot.day_of_week - 1);
    return { ...slot, resolved_weekday: ((d.getDay() + 6) % 7) + 1 };
  });
}

function dayLabels(): string[] {
  return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Day 1", "Day 3", "Day 5"]
    .flatMap((label) => screen.queryAllByText(label).map((el) => ({ label, el })))
    .sort((a, b) => (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1))
    .map(({ label }) => label);
}

beforeEach(() => {
  previewPlanningBlock.mockReset();
});

afterEach(cleanup);

describe("the block-create preview", () => {
  it("names the weekday each slot falls on, and refetches when the start date changes", async () => {
    previewPlanningBlock.mockImplementation((body: BlockCreateRequest) => Promise.resolve(resolvedFor(body)));
    render(<BlockCreateModal />);

    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-10-15" } }); // a Thursday
    await waitFor(() => expect(dayLabels()).toEqual(["Thursday", "Saturday", "Monday"]));
    expect(previewPlanningBlock).toHaveBeenLastCalledWith(
      expect.objectContaining({ start_date: "2026-10-15" }),
      TOKEN,
    );

    const calls = previewPlanningBlock.mock.calls.length;
    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-10-12" } }); // a Monday
    await waitFor(() => expect(dayLabels()).toEqual(["Monday", "Wednesday", "Friday"]));
    expect(previewPlanningBlock).toHaveBeenCalledTimes(calls + 1);
    expect(previewPlanningBlock).toHaveBeenLastCalledWith(
      expect.objectContaining({ start_date: "2026-10-12" }),
      TOKEN,
    );
  });

  it("never shows the previous start date's weekdays while the new preview is loading", async () => {
    const pending: Array<(slots: ReturnType<typeof resolvedFor>) => void> = [];
    previewPlanningBlock.mockImplementation(
      () => new Promise((resolve) => pending.push(resolve)),
    );
    render(<BlockCreateModal />);

    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-10-15" } });
    await waitFor(() => expect(previewPlanningBlock).toHaveBeenLastCalledWith(
      expect.objectContaining({ start_date: "2026-10-15" }), TOKEN,
    ));
    pending[pending.length - 1](resolvedFor({ start_date: "2026-10-15" } as BlockCreateRequest));
    await waitFor(() => expect(dayLabels()).toEqual(["Thursday", "Saturday", "Monday"]));

    // A new start date: its response is held back. The Thursday week must not stay on screen.
    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-10-12" } });
    await waitFor(() => expect(previewPlanningBlock).toHaveBeenLastCalledWith(
      expect.objectContaining({ start_date: "2026-10-12" }), TOKEN,
    ));
    expect(dayLabels()).toEqual([]);
    screen.getByText("Loading…");

    pending[pending.length - 1](resolvedFor({ start_date: "2026-10-12" } as BlockCreateRequest));
    await waitFor(() => expect(dayLabels()).toEqual(["Monday", "Wednesday", "Friday"]));
  });

  it("ignores a response that arrives after the inputs changed again", async () => {
    const pending = new Map<string, (slots: ReturnType<typeof resolvedFor>) => void>();
    previewPlanningBlock.mockImplementation(
      (body: BlockCreateRequest) => new Promise((resolve) => pending.set(body.start_date, resolve)),
    );
    render(<BlockCreateModal />);

    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-10-15" } });
    await waitFor(() => expect(pending.has("2026-10-15")).toBe(true));
    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-10-12" } });
    await waitFor(() => expect(pending.has("2026-10-12")).toBe(true));

    // The newer request answers first; the older, Thursday answer lands afterwards.
    pending.get("2026-10-12")!(resolvedFor({ start_date: "2026-10-12" } as BlockCreateRequest));
    await waitFor(() => expect(dayLabels()).toEqual(["Monday", "Wednesday", "Friday"]));
    pending.get("2026-10-15")!(resolvedFor({ start_date: "2026-10-15" } as BlockCreateRequest));
    await new Promise((r) => setTimeout(r, 0));
    expect(dayLabels()).toEqual(["Monday", "Wednesday", "Friday"]);
  });

  it("reads Day N against a server that sends no weekday", async () => {
    previewPlanningBlock.mockResolvedValue(WEEK);
    render(<BlockCreateModal />);

    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-10-15" } });
    await waitFor(() => expect(dayLabels()).toEqual(["Day 1", "Day 3", "Day 5"]));
    expect(screen.queryByText("Monday")).toBeNull();
  });
});
