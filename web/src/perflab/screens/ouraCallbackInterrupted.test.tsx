// @vitest-environment jsdom
//
// src/perflab/screens/ouraCallbackInterrupted.test.tsx
//
// Crossing 3 (ADR-0075): an Oura attempt started by a newer release (PKCE, P6b) returns to
// /settings/oura-callback on this release, e.g. across a rollback. Nothing here can finish it.
// Settings says so, drops the callback parameters and the abandoned verifier, and calls nothing.
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsScreen } from "./SettingsScreen";
import {
  OURA_INTERRUPTED_MESSAGE,
  OURA_PKCE_STORAGE_KEY,
  resetOuraReturnForTests,
} from "../wearableSync";

const oura = {
  getOuraAuthorizeUrl: vi.fn(),
  syncOura: vi.fn(),
  disconnectOura: vi.fn(),
  getWearableConnection: vi.fn(),
};

vi.mock("@/api/perfLabClient", () => ({
  getProfile: () => new Promise(() => undefined),
  submitStrengthEvidence: vi.fn(),
  listStrengthReports: () => Promise.resolve([]),
  listObjectives: () => Promise.resolve([]),
  getWearableConnection: (...a: unknown[]) => oura.getWearableConnection(...a),
  getOuraAuthorizeUrl: (...a: unknown[]) => oura.getOuraAuthorizeUrl(...a),
  syncOura: (...a: unknown[]) => oura.syncOura(...a),
  disconnectOura: (...a: unknown[]) => oura.disconnectOura(...a),
  listIngestTokens: () => Promise.resolve([]),
  wellnessIngestUrl: () => "/v1/wellness/ingest",
  createIngestToken: vi.fn(),
  revokeIngestToken: vi.fn(),
  updateProfile: vi.fn(),
  createObjective: vi.fn(),
  deleteObjective: vi.fn(),
}));

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({
    token: "athlete-token",
    isAuthenticated: true,
    isGuest: false,
    profile: null,
    user: { email: "athlete@example.com" },
    email: "athlete@example.com",
    refreshProfile: vi.fn(),
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

const CALLBACK = "/settings/oura-callback?code=SECRET-CODE&state=SECRET-STATE";

beforeEach(() => {
  resetOuraReturnForTests();
  Object.values(oura).forEach((fn) => fn.mockReset());
  oura.getWearableConnection.mockResolvedValue({ connected: false, connection: null });
  window.sessionStorage.clear();
  window.sessionStorage.setItem(OURA_PKCE_STORAGE_KEY, JSON.stringify({ state: "s", verifier: "v" }));
  window.sessionStorage.setItem("unrelated", "kept");
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("an Oura callback from a newer release", () => {
  for (const strict of [false, true]) {
    it(`says it was interrupted, cleans up, and calls nothing${strict ? " (StrictMode)" : ""}`, async () => {
      window.history.replaceState(null, "", CALLBACK);
      const ui = <SettingsScreen />;
      render(strict ? <React.StrictMode>{ui}</React.StrictMode> : ui);

      await waitFor(() => expect(screen.getByText(OURA_INTERRUPTED_MESSAGE)).toBeTruthy());
      expect(window.location.pathname).toBe("/settings");
      expect(window.location.search).toBe("");
      expect(window.location.href).not.toContain("SECRET");
      expect(window.sessionStorage.getItem(OURA_PKCE_STORAGE_KEY)).toBeNull();
      expect(window.sessionStorage.getItem("unrelated")).toBe("kept");
      expect(oura.getOuraAuthorizeUrl).not.toHaveBeenCalled();
      expect(oura.syncOura).not.toHaveBeenCalled();
      expect(oura.disconnectOura).not.toHaveBeenCalled();
    });
  }

  it("keeps the legacy return message under StrictMode too", async () => {
    // The same replay used to wipe this one: load() clears `error` when it runs again.
    window.history.replaceState(null, "", "/settings?oura=error");
    render(
      <React.StrictMode>
        <SettingsScreen />
      </React.StrictMode>,
    );
    await waitFor(() => expect(oura.getWearableConnection).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText("Oura sign-in didn't complete. Try Connect Oura again.")).toBeTruthy(),
    );
    expect(window.location.search).toBe("");
    // The legacy return is not an interrupted PKCE attempt: its verifier is left alone.
    expect(window.sessionStorage.getItem(OURA_PKCE_STORAGE_KEY)).not.toBeNull();
  });

  it("leaves an ordinary Settings visit alone", async () => {
    window.history.replaceState(null, "", "/settings");
    render(<SettingsScreen />);
    await waitFor(() => expect(oura.getWearableConnection).toHaveBeenCalled());
    expect(screen.queryByText(OURA_INTERRUPTED_MESSAGE)).toBeNull();
    expect(window.sessionStorage.getItem(OURA_PKCE_STORAGE_KEY)).not.toBeNull();
  });
});
