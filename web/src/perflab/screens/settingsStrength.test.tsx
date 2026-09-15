// @vitest-environment jsdom
//
// src/perflab/screens/settingsStrength.test.tsx
//
// Settings → Performance profile → Strength, rendered as a whole screen (the editor is not
// exported). A lift is updated by sending a characterized report, never by overwriting the
// profile number; the displayed value is the server's projection, so after a save the screen
// must READ IT BACK rather than echo what was typed. Weights are shown and typed in the
// selected unit and reach the API in kilograms.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { lbsToKg } from "@/lib/units";
import type { ProfileRead } from "@/types";
import { SettingsScreen } from "./SettingsScreen";

const TOKEN = "athlete-token";
const getProfile = vi.fn();
const submitStrengthEvidence = vi.fn();
const refreshProfile = vi.fn();

vi.mock("@/api/perfLabClient", () => ({
  getProfile: (...args: unknown[]) => getProfile(...args),
  submitStrengthEvidence: (...args: unknown[]) => submitStrengthEvidence(...args),
  listObjectives: () => Promise.resolve([]),
  getWearableConnection: () => Promise.resolve({ connected: false, connection: null }),
  updateProfile: vi.fn(),
  createObjective: vi.fn(),
  deleteObjective: vi.fn(),
}));

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({
    token: TOKEN,
    isAuthenticated: true,
    isGuest: false,
    profile: null,
    user: { email: "athlete@example.com" },
    email: "athlete@example.com",
    refreshProfile: (...args: unknown[]) => refreshProfile(...args),
    logout: vi.fn(),
  }),
}));

let units = "Metric (km)";
const storeActions = { setSetting: vi.fn(), refreshObjectives: vi.fn(), openAuth: vi.fn() };

vi.mock("../store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../store")>();
  return {
    ...actual,
    usePerfLab: () => ({
      state: {
        settings: {
          sex: "Female", units, accent: "#c6f135", goal: "Strength",
          notifReadiness: true, notifTissue: true, notifWeekly: false,
        },
        objectivesRefreshKey: 0,
      },
      actions: storeActions,
    }),
  };
});

const profile = (over: Partial<ProfileRead> = {}): ProfileRead => ({
  available_days_per_week: 4,
  bench_1rm_kg: null,
  bodyweight_kg: null,
  deadlift_1rm_kg: null,
  display_name: null,
  equipment: [],
  experience_level: "intermediate",
  experience_years: 3,
  height_cm: null,
  overhead_1rm_kg: null,
  primary_goal: null,
  pullup_max_reps: null,
  run_1p5mi_seconds: null,
  run_5k_seconds: null,
  session_duration_minutes: 60,
  squat_1rm_kg: null,
  ...over,
});

/** The Squat row: its label, its current value, and its Update button. */
const squatRow = () => within(screen.getByText("Squat").parentElement as HTMLElement);

async function openSquat(shown: string) {
  await waitFor(() => squatRow().getByText(shown));
  fireEvent.click(squatRow().getByRole("button", { name: "Update" }));
}

beforeEach(() => {
  units = "Metric (km)";
  getProfile.mockReset();
  submitStrengthEvidence.mockReset();
  refreshProfile.mockReset();
  refreshProfile.mockResolvedValue(undefined);
});

afterEach(cleanup);

describe("updating a lift in Settings", () => {
  it("sends a report with the session, then shows the projection read back from the server", async () => {
    getProfile
      .mockResolvedValueOnce(profile({ squat_1rm_kg: 140 }))
      .mockResolvedValueOnce(profile({ squat_1rm_kg: 150 }));
    submitStrengthEvidence.mockResolvedValue({});
    render(<SettingsScreen />);

    await openSquat("140 kg");
    fireEvent.change(screen.getByLabelText("Squat: Weight in kg"), { target: { value: "150" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => squatRow().getByText("150 kg"));
    expect(submitStrengthEvidence).toHaveBeenCalledWith(
      { benchmark_code: "pl_e1rm_squat", method: "tested_max", value_kg: 150, collection_mode: "retest" },
      TOKEN,
    );
    expect(getProfile).toHaveBeenCalledTimes(2);
    expect(getProfile).toHaveBeenLastCalledWith(TOKEN);
    expect(refreshProfile).toHaveBeenCalled();
    expect(screen.queryByLabelText("Squat: Weight in kg")).toBeNull();
  });

  it("keeps the form open with the error, and reloads nothing, when the report is refused", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    submitStrengthEvidence.mockRejectedValue({ message: "Report refused.", status: 422 });
    render(<SettingsScreen />);

    await openSquat("140 kg");
    fireEvent.change(screen.getByLabelText("Squat: Weight in kg"), { target: { value: "150" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("Report refused.");
    expect((screen.getByLabelText("Squat: Weight in kg") as HTMLInputElement).value).toBe("150");
    expect(getProfile).toHaveBeenCalledTimes(1);
    squatRow().getByText("140 kg");
  });
});

describe("units in Settings", () => {
  it("shows and takes pounds for an imperial athlete, and sends kilograms", async () => {
    units = "Imperial (mi)";
    getProfile
      .mockResolvedValueOnce(profile({ squat_1rm_kg: 140 }))
      .mockResolvedValueOnce(profile({ squat_1rm_kg: 142.9 }));
    submitStrengthEvidence.mockResolvedValue({});
    render(<SettingsScreen />);

    await openSquat("309 lb");
    fireEvent.change(screen.getByLabelText("Squat: Weight in lb"), { target: { value: "315" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => squatRow().getByText("315 lb"));
    expect(submitStrengthEvidence).toHaveBeenCalledWith(
      {
        benchmark_code: "pl_e1rm_squat",
        method: "tested_max",
        value_kg: lbsToKg(315),
        collection_mode: "retest",
      },
      TOKEN,
    );
  });

  it("keeps an open report in the unit it was typed in when the unit setting changes", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    submitStrengthEvidence.mockResolvedValue({});
    const { rerender } = render(<SettingsScreen />);

    await openSquat("140 kg");
    fireEvent.change(screen.getByLabelText("Squat: Weight in kg"), { target: { value: "150" } });
    units = "Imperial (mi)";
    rerender(<SettingsScreen />);
    screen.getByLabelText("Squat: Weight in kg");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(1));
    expect(submitStrengthEvidence.mock.calls[0][0]).toMatchObject({ value_kg: 150 });
  });
});
