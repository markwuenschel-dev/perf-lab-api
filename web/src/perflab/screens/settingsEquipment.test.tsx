// @vitest-environment jsdom
//
// src/perflab/screens/settingsEquipment.test.tsx
//
// Settings → Equipment (S-C). Two controls that must stay different things:
//   • "What you have" writes the hard `equipment` list, with three distinguishable stored states —
//     not set ([]), bodyweight only (["bodyweight"]), or equipment tags — and never saves an empty
//     list while claiming "I have equipment";
//   • "Preferred equipment" writes `equipment_preference` only, and says "No preference" when empty.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProfileRead, ProfileUpdate } from "@/types";
import { SettingsScreen } from "./SettingsScreen";

const TOKEN = "athlete-token";
const updateProfile = vi.fn();
const refreshProfile = vi.fn();
let authProfile: ProfileRead | null = null;

vi.mock("@/api/perfLabClient", () => ({
  getProfile: () => new Promise(() => undefined),
  submitStrengthEvidence: vi.fn(),
  listObjectives: () => Promise.resolve([]),
  getWearableConnection: () => Promise.resolve({ connected: false, connection: null }),
  updateProfile: (...args: unknown[]) => updateProfile(...args),
  createObjective: vi.fn(),
  deleteObjective: vi.fn(),
}));

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({
    token: TOKEN,
    isAuthenticated: true,
    isGuest: false,
    profile: authProfile,
    user: { email: "athlete@example.com" },
    email: "athlete@example.com",
    refreshProfile: (...args: unknown[]) => refreshProfile(...args),
    logout: vi.fn(),
  }),
}));

vi.mock("../store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../store")>();
  return {
    ...actual,
    usePerfLab: () => ({
      state: {
        settings: {
          sex: "Female", units: "Metric (km)", accent: "#c6f135", goal: "Strength",
          notifReadiness: true, notifTissue: true, notifWeekly: false,
        },
        objectivesRefreshKey: 0,
      },
      actions: { setSetting: vi.fn(), refreshObjectives: vi.fn(), openAuth: vi.fn() },
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
  equipment_preference: [],
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

const haveGroup = () => within(screen.getByRole("radiogroup", { name: "What you have" }));
const preferenceGroup = () => within(screen.getByRole("group", { name: "Preferred equipment" }));

beforeEach(() => {
  authProfile = profile();
  updateProfile.mockReset();
  updateProfile.mockImplementation((patch: ProfileUpdate) =>
    Promise.resolve(
      profile({
        equipment: patch.equipment ?? authProfile?.equipment ?? [],
        equipment_preference: patch.equipment_preference ?? authProfile?.equipment_preference ?? [],
      }),
    ),
  );
  refreshProfile.mockReset();
  refreshProfile.mockResolvedValue(undefined);
});

afterEach(cleanup);

describe("what the athlete has", () => {
  it("starts as not set, with no preference, for a new athlete", () => {
    render(<SettingsScreen />);
    expect(haveGroup().getByRole("radio", { name: "Not set" }).getAttribute("aria-checked")).toBe("true");
    expect(preferenceGroup().getByText("No preference")).toBeTruthy();
  });

  it("saves bodyweight only as its own state, distinct from not set", async () => {
    render(<SettingsScreen />);
    fireEvent.click(haveGroup().getByRole("radio", { name: "Bodyweight only" }));
    await waitFor(() => expect(updateProfile).toHaveBeenCalledWith({ equipment: ["bodyweight"] }, TOKEN));
  });

  it("saves equipment only once a tag is chosen, and removing the last tag returns to not set", async () => {
    render(<SettingsScreen />);
    fireEvent.click(haveGroup().getByRole("radio", { name: "I have equipment" }));
    expect(updateProfile).not.toHaveBeenCalled();

    const owned = within(screen.getByRole("group", { name: "Equipment you have" }));
    fireEvent.click(owned.getByRole("button", { name: "Barbell" }));
    await waitFor(() => expect(updateProfile).toHaveBeenLastCalledWith({ equipment: ["barbell"] }, TOKEN));

    fireEvent.click(within(screen.getByRole("group", { name: "Equipment you have" })).getByRole("button", { name: "Barbell" }));
    await waitFor(() => expect(updateProfile).toHaveBeenLastCalledWith({ equipment: [] }, TOKEN));
    expect(haveGroup().getByRole("radio", { name: "Not set" }).getAttribute("aria-checked")).toBe("true");
  });

  it("reads a stored bodyweight-only list back as bodyweight only", () => {
    authProfile = profile({ equipment: ["bodyweight"] });
    render(<SettingsScreen />);
    expect(haveGroup().getByRole("radio", { name: "Bodyweight only" }).getAttribute("aria-checked")).toBe("true");
  });
});

describe("the equipment preference", () => {
  it("sends only the preference, in a fixed order, and never the equipment list", async () => {
    authProfile = profile({ equipment: ["barbell"], equipment_preference: ["machine"] });
    render(<SettingsScreen />);
    fireEvent.click(preferenceGroup().getByRole("button", { name: "Dumbbells" }));
    await waitFor(() =>
      expect(updateProfile).toHaveBeenCalledWith({ equipment_preference: ["dumbbell", "machine"] }, TOKEN),
    );
    const patch = updateProfile.mock.calls[0][0] as ProfileUpdate;
    expect(patch).not.toHaveProperty("equipment");
  });

  it("clears back to no preference", async () => {
    authProfile = profile({ equipment_preference: ["dumbbell"] });
    render(<SettingsScreen />);
    fireEvent.click(preferenceGroup().getByRole("button", { name: "Dumbbells" }));
    await waitFor(() => expect(updateProfile).toHaveBeenCalledWith({ equipment_preference: [] }, TOKEN));
    await waitFor(() => expect(preferenceGroup().getByText("No preference")).toBeTruthy());
  });
});
