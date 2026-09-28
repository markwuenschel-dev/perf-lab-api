// @vitest-environment jsdom
//
// src/perflab/overlays/checkinDevice.test.tsx
//
// The morning check-in with a connected wearable: it syncs when stale, shows today's
// device-measured HRV / resting HR / sleep as the device's values instead of asking for
// them, and the saved manual sample carries only what the athlete actually reported.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CheckinModal } from "./CheckinModal";

const TODAY = new Date().toISOString().slice(0, 10);
const getWearableConnection = vi.fn();
const syncOura = vi.fn();
const listWellness = vi.fn();
const ingestWellness = vi.fn();

vi.mock("@/api/perfLabClient", () => ({
  getWearableConnection: (...a: unknown[]) => getWearableConnection(...a),
  syncOura: (...a: unknown[]) => syncOura(...a),
  listWellness: (...a: unknown[]) => listWellness(...a),
  ingestWellness: (...a: unknown[]) => ingestWellness(...a),
  getReadiness: () => Promise.resolve({ score: 70, confidence: null }),
  updateProfile: vi.fn(),
}));

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token: "athlete-token", profile: { untracked_wellness_signals: [] } }),
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
  getWearableConnection.mockResolvedValue({ connected: true, connection: { last_sync_at: null } });
  syncOura.mockResolvedValue({ rows_written: 1 });
  listWellness.mockResolvedValue([
    {
      id: 9, user_id: 1, date: TODAY, source: "oura", created_at: `${TODAY}T07:00:00`,
      hrv_ms: 62, resting_hr: 48, sleep_hours: 7.4, sleep_quality: 88,
      soreness: null, mood: null, stress: null, raw: {},
    },
  ]);
  ingestWellness.mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("syncs, shows the ring's readings, and saves only the athlete's own signals", async () => {
  render(<CheckinModal />);

  await waitFor(() => expect(screen.getByTestId("device-hrv")).toBeTruthy());
  expect(syncOura).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId("device-hrv").textContent).toContain("62 ms");
  expect(screen.getByTestId("device-rhr").textContent).toContain("48 bpm");
  expect(screen.getByTestId("device-sleep").textContent).toContain("7.4 h");
  expect(screen.getByTestId("device-sleep").textContent).toContain("from Oura");
  // The device-owned sliders are gone; the subjective ones remain.
  expect(screen.queryByText("Sleep duration")).toBeNull();
  expect(screen.queryByText("Sleep quality")).toBeNull();
  expect(screen.getAllByText("HRV (overnight)")).toHaveLength(1); // the device row, no slider
  expect(screen.getByText("Stress")).toBeTruthy();

  fireEvent.click(screen.getByText("Set today's readiness →"));
  await waitFor(() => expect(ingestWellness).toHaveBeenCalledTimes(1));
  const body = ingestWellness.mock.calls[0][0];
  expect(body).not.toHaveProperty("hrv_ms");
  expect(body).not.toHaveProperty("resting_hr");
  expect(body).not.toHaveProperty("sleep_hours");
  expect(body).not.toHaveProperty("sleep_quality");
  expect(body).toMatchObject({ source: "manual", soreness: 3, mood: 8, stress: 4 });
});

it("without a wearable reading today, the check-in asks for everything by hand", async () => {
  listWellness.mockResolvedValue([]);
  render(<CheckinModal />);
  await waitFor(() => expect(listWellness).toHaveBeenCalled());
  expect(screen.queryByTestId("device-hrv")).toBeNull();
  expect(screen.getByText("Sleep duration")).toBeTruthy();

  fireEvent.click(screen.getByText("Set today's readiness →"));
  await waitFor(() => expect(ingestWellness).toHaveBeenCalledTimes(1));
  expect(ingestWellness.mock.calls[0][0]).toMatchObject({ hrv_ms: 50, resting_hr: 60, sleep_hours: 6 });
});
