// @vitest-environment jsdom
//
// Settings → Timezone (B1). Shows which calendar the athlete's days are counted in — saying so
// plainly while the zone is unknown (UTC, with a day of grace) — and changes it only on an
// explicit save, as the athlete's own choice.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TIMEZONE_UNSUPPORTED, TimezoneCard } from "./SettingsScreen";

const updateProfile = vi.fn();
const refreshProfile = vi.fn();
let authProfile: { timezone?: string | null; timezone_source?: string | null } | null = null;

vi.mock("@/api/perfLabClient", () => ({
  updateProfile: (...args: unknown[]) => updateProfile(...args),
}));

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({
    token: "athlete-token",
    profile: authProfile,
    refreshProfile: (...args: unknown[]) => refreshProfile(...args),
  }),
}));

beforeEach(() => {
  updateProfile.mockReset();
  updateProfile.mockImplementation((patch: { timezone?: string }) =>
    Promise.resolve({ timezone: patch.timezone, timezone_source: "athlete" }),
  );
  refreshProfile.mockReset();
  refreshProfile.mockResolvedValue(undefined);
});

afterEach(cleanup);

describe("the timezone card", () => {
  it("says an unknown zone is counted in UTC with a day of grace", () => {
    authProfile = { timezone: null, timezone_source: null };
    render(<TimezoneCard />);
    expect(screen.getByTestId("timezone-status").textContent).toMatch(/Unknown — your days are counted in UTC/);
  });

  it("names a detected zone as detected, and a chosen one plainly", () => {
    authProfile = { timezone: "America/New_York", timezone_source: "browser" };
    const { unmount } = render(<TimezoneCard />);
    expect(screen.getByTestId("timezone-status").textContent).toBe(
      "Your days are counted in America/New_York (detected from your browser).",
    );
    unmount();
    authProfile = { timezone: "UTC", timezone_source: "athlete" };
    render(<TimezoneCard />);
    expect(screen.getByTestId("timezone-status").textContent).toBe("Your days are counted in UTC.");
  });

  it("saves a new zone only when the athlete presses Save", async () => {
    authProfile = { timezone: "America/New_York", timezone_source: "browser" };
    render(<TimezoneCard />);
    const select = screen.getByRole("combobox", { name: "Timezone" }) as HTMLSelectElement;
    expect(select.value).toBe("America/New_York");
    fireEvent.change(select, { target: { value: "UTC" } });
    expect(updateProfile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save timezone" }));
    await waitFor(() => expect(updateProfile).toHaveBeenCalledWith({ timezone: "UTC" }, "athlete-token"));
    await waitFor(() => expect(refreshProfile).toHaveBeenCalled());
    expect(screen.getByTestId("timezone-saved").textContent).toBe("Saved — your days are now counted in UTC.");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says so when an API from before B1 answers 200 without storing the zone", async () => {
    authProfile = { timezone: null, timezone_source: null };
    // The old ProfileUpdate ignores the unknown key; its ProfileRead has no zone at all.
    updateProfile.mockResolvedValue({ display_name: null });
    render(<TimezoneCard />);
    fireEvent.change(screen.getByRole("combobox", { name: "Timezone" }), { target: { value: "UTC" } });
    fireEvent.click(screen.getByRole("button", { name: "Save timezone" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(TIMEZONE_UNSUPPORTED));
    expect(refreshProfile).not.toHaveBeenCalled();
    expect(screen.queryByTestId("timezone-saved")).toBeNull();
  });

  it("shows an old API's profile as unsupported, not as an unknown zone", () => {
    authProfile = {};
    render(<TimezoneCard />);
    expect(screen.getByTestId("timezone-status").textContent).toBe(TIMEZONE_UNSUPPORTED);
    expect(screen.queryByRole("combobox", { name: "Timezone" })).toBeNull();
  });

  it("treats a profile that did not load as not loaded, not as an unknown zone", () => {
    authProfile = null;
    render(<TimezoneCard />);
    expect(screen.getByTestId("timezone-status").textContent).toMatch(/hasn't loaded/);
    expect(screen.getByTestId("timezone-status").textContent).not.toMatch(/Unknown/);
    expect(screen.queryByRole("combobox", { name: "Timezone" })).toBeNull();
  });
});
