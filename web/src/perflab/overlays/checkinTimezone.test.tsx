// @vitest-environment jsdom
//
// B1: the check-in reads today's device row and posts the manual sample on the ATHLETE's day
// (their stored timezone), the day the backend's readiness counts as fresh — not the UTC day.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CheckinModal } from "./CheckinModal";

const listWellness = vi.fn();
const ingestWellness = vi.fn();

vi.mock("@/api/perfLabClient", () => ({
  getWearableConnection: () => Promise.resolve({ connected: false, connection: null }),
  syncOura: vi.fn(),
  listWellness: (...a: unknown[]) => listWellness(...a),
  ingestWellness: (...a: unknown[]) => ingestWellness(...a),
  getReadiness: () => Promise.resolve({ score: 70, confidence: null }),
  updateProfile: vi.fn(),
}));

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({
    token: "athlete-token",
    profile: { untracked_wellness_signals: [], timezone: "America/Los_Angeles", timezone_source: "browser" },
  }),
}));

vi.mock("../store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../store")>();
  return {
    ...actual,
    usePerfLab: () => ({
      state: {
        checkinOpen: true,
        checkin: { hrv: 50, sleepH: 6, sleepQ: 3, rhr: 60, soreness: "mild", mood: 4, stress: 2, done: false },
      },
      actions: { setCheckin: vi.fn(), closeCheckin: vi.fn(), applyCheckin: vi.fn(), cacheReadiness: vi.fn() },
    }),
  };
});

beforeEach(() => {
  // 03:00Z on Jan 15 is 19:00 on Jan 14 in Los Angeles. Only Date is faked; timers stay real.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-01-15T03:00:00Z"));
  listWellness.mockResolvedValue([
    {
      id: 9, user_id: 1, date: "2026-01-14", source: "oura", created_at: "2026-01-14T15:00:00",
      hrv_ms: 62, resting_hr: 48, sleep_hours: 7.4, sleep_quality: 88,
      soreness: null, mood: null, stress: null, raw: {},
    },
  ]);
  ingestWellness.mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

it("shows the device row and posts the sample on the athlete's own day", async () => {
  render(<CheckinModal />);
  await waitFor(() => expect(screen.getByTestId("device-hrv")).toBeTruthy());
  fireEvent.click(screen.getByText("Set today's readiness →"));
  await waitFor(() => expect(ingestWellness).toHaveBeenCalledTimes(1));
  expect(ingestWellness.mock.calls[0][0]).toMatchObject({ date: "2026-01-14", source: "manual" });
});
