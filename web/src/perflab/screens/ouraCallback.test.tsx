// @vitest-environment jsdom
//
// src/perflab/screens/ouraCallback.test.tsx
//
// Connecting Oura is a PKCE attempt the server decides (P6b, ADR-0075). This tab starts it only
// with a consent URL bound to its own challenge, and when Oura sends the browser back it finishes
// exactly the attempt it stored, for the identity that started it. Inputs are kept until the
// outcome is terminal, so a lost reply or an expired session can be resumed. "Connected" is what
// GET /connection says, never a receipt. Every case runs under StrictMode, as the app does.
import React from "react";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsScreen } from "./SettingsScreen";
import { OURA_MESSAGES, useOuraAttempt } from "./useOuraAttempt";
import { loadAttempt, ouraNav, saveAttempt, type OuraAttemptRecord } from "../ouraPkce";
import {
  OURA_PKCE_STORAGE_KEY,
  OURA_UPDATE_INTERRUPTED_MESSAGE,
  resetOuraReturnForTests,
} from "../wearableSync";

const oura = {
  getOuraAuthorizeUrl: vi.fn(),
  completeOuraAuthorization: vi.fn(),
  cancelOuraAuthorization: vi.fn(),
  getWearableConnection: vi.fn(),
  syncOura: vi.fn(),
  disconnectOura: vi.fn(),
};

vi.mock("@/api/perfLabClient", () => ({
  getProfile: () => new Promise(() => undefined),
  submitStrengthEvidence: vi.fn(),
  listStrengthReports: () => Promise.resolve([]),
  listObjectives: () => Promise.resolve([]),
  getWearableConnection: (...a: unknown[]) => oura.getWearableConnection(...a),
  getOuraAuthorizeUrl: (...a: unknown[]) => oura.getOuraAuthorizeUrl(...a),
  completeOuraAuthorization: (...a: unknown[]) => oura.completeOuraAuthorization(...a),
  cancelOuraAuthorization: (...a: unknown[]) => oura.cancelOuraAuthorization(...a),
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

const EMAIL = "athlete@example.com";
let auth: { token: string | null; email: string } = { token: "t1", email: EMAIL };

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({
    token: auth.token,
    isAuthenticated: auth.token !== null,
    isGuest: false,
    profile: null,
    user: auth.token ? { email: auth.email } : null,
    email: auth.email,
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

const NOT_CONNECTED = { connected: false, connection: null };
const CONNECTED = {
  connected: true,
  connection: { provider: "oura", auth_type: "oauth", last_sync_at: null, expires_at: null, scope: null },
};
const SUCCEEDED = {
  status: "succeeded", reason: null, completed_at: "2026-10-13T10:00:00",
  linked: { connection_id: 7, generation: 2 },
};
const VERIFIER = "v".repeat(64);

function stored(state: string, extra: Partial<OuraAttemptRecord> = {}): OuraAttemptRecord {
  const rec: OuraAttemptRecord = {
    state, verifier: VERIFIER, challenge: "c".repeat(43), startedAt: 1, userKey: EMAIL, ...extra,
  };
  saveAttempt(rec);
  return rec;
}

function apiError(status: number, detail?: unknown) {
  return { message: `HTTP ${status}`, status, details: detail === undefined ? undefined : { detail } };
}

function renderStrict() {
  return render(
    <React.StrictMode>
      <SettingsScreen />
    </React.StrictMode>,
  );
}

let assign: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  resetOuraReturnForTests();
  Object.values(oura).forEach((fn) => fn.mockReset());
  oura.getWearableConnection.mockResolvedValue(NOT_CONNECTED);
  auth = { token: "t1", email: EMAIL };
  window.sessionStorage.clear();
  assign = vi.spyOn(ouraNav, "assign").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  assign.mockRestore();
  window.history.replaceState(null, "", "/");
});

describe("Connect Oura", () => {
  it("opens the consent URL only when it carries this tab's challenge", async () => {
    oura.getOuraAuthorizeUrl.mockImplementation((_t: string, ch: string) =>
      Promise.resolve({
        authorize_url: `https://cloud.ouraring.com/oauth/authorize?state=S1&code_challenge=${ch}&code_challenge_method=S256`,
      }),
    );
    window.history.replaceState(null, "", "/settings");
    renderStrict();
    fireEvent.click(await screen.findByText("Connect Oura"));
    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1));
    const [, challenge] = oura.getOuraAuthorizeUrl.mock.calls[0];
    expect(assign.mock.calls[0][0]).toContain(`code_challenge=${challenge}`);
    const rec = loadAttempt();
    expect(rec).toMatchObject({ state: "S1", challenge, userKey: EMAIL });
    expect(rec?.verifier).toMatch(/^[A-Za-z0-9\-._~]{64}$/);
  });

  it("refuses a consent URL without its challenge: no navigation, nothing stored", async () => {
    oura.getOuraAuthorizeUrl.mockResolvedValue({
      authorize_url: "https://cloud.ouraring.com/oauth/authorize?state=S1",
    });
    window.history.replaceState(null, "", "/settings");
    renderStrict();
    fireEvent.click(await screen.findByText("Connect Oura"));
    expect(await screen.findByText(OURA_MESSAGES.reload)).toBeTruthy();
    expect(assign).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem(OURA_PKCE_STORAGE_KEY)).toBeNull();
  });

  it("shows the server's refusal (a 400 from authorize)", async () => {
    oura.getOuraAuthorizeUrl.mockRejectedValue({ message: "Perf Lab was updated. Reload the page, then Connect Oura again.", status: 400 });
    window.history.replaceState(null, "", "/settings");
    renderStrict();
    fireEvent.click(await screen.findByText("Connect Oura"));
    expect(await screen.findByText(OURA_MESSAGES.reload)).toBeTruthy();
    expect(assign).not.toHaveBeenCalled();
  });
});

describe("coming back from Oura", () => {
  it("finishes the stored attempt once, cleans the URL, and says connected only from /connection", async () => {
    stored("S1");
    oura.completeOuraAuthorization.mockResolvedValue(SUCCEEDED);
    oura.getWearableConnection.mockResolvedValueOnce(NOT_CONNECTED).mockResolvedValue(CONNECTED);
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.connected)).toBeTruthy();
    expect(oura.completeOuraAuthorization).toHaveBeenCalledTimes(1);
    expect(oura.completeOuraAuthorization).toHaveBeenCalledWith(
      { state: "S1", code: "C1", code_verifier: VERIFIER }, "t1",
    );
    expect(window.location.pathname + window.location.search).toBe("/settings");
    expect(loadAttempt()).toBeNull();
  });

  it("does not claim connected when /connection says otherwise, or cannot be read", async () => {
    stored("S1");
    oura.completeOuraAuthorization.mockResolvedValue(SUCCEEDED);
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.linkedButNotShown)).toBeTruthy();
    expect(screen.queryByText(OURA_MESSAGES.connected)).toBeNull();

    cleanup();
    resetOuraReturnForTests();
    stored("S2");
    oura.getWearableConnection.mockReset();
    oura.getWearableConnection.mockResolvedValueOnce(NOT_CONNECTED).mockRejectedValue(new TypeError("net"));
    window.history.replaceState(null, "", "/settings/oura-callback?code=C2&state=S2");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.linkedStatusUnknown)).toBeTruthy();
    expect(screen.queryByText(OURA_MESSAGES.connected)).toBeNull();
  });

  it("leaves another attempt alone when the link isn't this tab's", async () => {
    stored("S2");
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.mismatch)).toBeTruthy();
    expect(loadAttempt()?.state).toBe("S2");
    expect(loadAttempt()?.callback).toBeUndefined();
    expect(oura.completeOuraAuthorization).not.toHaveBeenCalled();
    expect(oura.cancelOuraAuthorization).not.toHaveBeenCalled();
    expect(window.location.search).toBe("");
  });

  it("matches the state before acting on a denial", async () => {
    stored("S2");
    window.history.replaceState(null, "", "/settings/oura-callback?error=access_denied&state=S1");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.mismatch)).toBeTruthy();
    expect(oura.cancelOuraAuthorization).not.toHaveBeenCalled();
    expect(loadAttempt()?.state).toBe("S2");
  });

  it("cancels a matching denied attempt", async () => {
    stored("S1");
    oura.cancelOuraAuthorization.mockResolvedValue({ status: "failed", reason: "cancelled", completed_at: null, linked: null });
    window.history.replaceState(null, "", "/settings/oura-callback?error=access_denied&state=S1");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.cancelled)).toBeTruthy();
    expect(oura.cancelOuraAuthorization).toHaveBeenCalledTimes(1);
    expect(oura.cancelOuraAuthorization).toHaveBeenCalledWith({ state: "S1", code_verifier: VERIFIER }, "t1");
    expect(oura.completeOuraAuthorization).not.toHaveBeenCalled();
    expect(loadAttempt()).toBeNull();
  });

  it("never says cancelled when the attempt had already succeeded", async () => {
    stored("S1");
    oura.cancelOuraAuthorization.mockResolvedValue(SUCCEEDED);
    oura.getWearableConnection.mockResolvedValueOnce(NOT_CONNECTED).mockResolvedValue(CONNECTED);
    window.history.replaceState(null, "", "/settings/oura-callback?error=access_denied&state=S1");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.connected)).toBeTruthy();
    expect(screen.queryByText(OURA_MESSAGES.cancelled)).toBeNull();
  });

  it("keeps the inputs after a lost reply, and Retry resends them", async () => {
    stored("S1");
    oura.completeOuraAuthorization
      .mockRejectedValueOnce(new TypeError("Failed to fetch")) // the success committed; the reply was lost
      .mockResolvedValueOnce(SUCCEEDED);
    oura.getWearableConnection.mockResolvedValueOnce(NOT_CONNECTED).mockResolvedValue(CONNECTED);
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.stillFinishing)).toBeTruthy();
    expect(loadAttempt()?.callback).toEqual({ code: "C1", error: undefined });

    fireEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText(OURA_MESSAGES.connected)).toBeTruthy();
    expect(oura.completeOuraAuthorization).toHaveBeenCalledTimes(2);
    expect(oura.completeOuraAuthorization.mock.calls[1]).toEqual(oura.completeOuraAuthorization.mock.calls[0]);
    expect(loadAttempt()).toBeNull();
  });

  it("keeps the inputs while the server says in progress", async () => {
    stored("S1");
    oura.completeOuraAuthorization.mockRejectedValue(apiError(409, { status: "in_progress", reason: null }));
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.stillFinishing)).toBeTruthy();
    expect(screen.getByText("Retry")).toBeTruthy();
    expect(loadAttempt()?.state).toBe("S1");
  });

  it("clears a failed attempt and says why", async () => {
    stored("S1");
    oura.completeOuraAuthorization.mockRejectedValue(apiError(409, { status: "failed", reason: "expired" }));
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.expired)).toBeTruthy();
    expect(loadAttempt()).toBeNull();
  });

  it("keeps the inputs through a 401 and finishes after signing in again", async () => {
    stored("S1");
    oura.completeOuraAuthorization
      .mockRejectedValueOnce(apiError(401))
      .mockResolvedValueOnce(SUCCEEDED);
    oura.getWearableConnection.mockResolvedValueOnce(NOT_CONNECTED).mockResolvedValue(CONNECTED);
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    const ui = renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.signIn)).toBeTruthy();
    expect(loadAttempt()?.callback?.code).toBe("C1");

    auth = { token: null, email: EMAIL }; // the session layer cleared it
    ui.rerender(<React.StrictMode><SettingsScreen /></React.StrictMode>);
    auth = { token: "t2", email: EMAIL }; // signed in again, same account
    ui.rerender(<React.StrictMode><SettingsScreen /></React.StrictMode>);
    expect(await screen.findByText(OURA_MESSAGES.connected)).toBeTruthy();
    expect(oura.completeOuraAuthorization).toHaveBeenLastCalledWith(
      { state: "S1", code: "C1", code_verifier: VERIFIER }, "t2",
    );
  });

  it("does not resume for a different account", async () => {
    stored("S1", { callback: { code: "C1" } });
    auth = { token: "t9", email: "someone-else@example.com" };
    window.history.replaceState(null, "", "/settings");
    renderStrict();
    await waitFor(() => expect(oura.getWearableConnection).toHaveBeenCalled());
    expect(oura.completeOuraAuthorization).not.toHaveBeenCalled();
    expect(loadAttempt()?.state).toBe("S1");
  });

  it("drops a late reply for an attempt the athlete abandoned (A pending → Start over → B)", async () => {
    stored("A");
    let replyA!: (v: unknown) => void;
    oura.completeOuraAuthorization
      .mockRejectedValueOnce(apiError(409, { status: "in_progress", reason: null }))
      .mockImplementationOnce(() => new Promise((r) => (replyA = r)));
    oura.getOuraAuthorizeUrl.mockImplementation((_t: string, ch: string) =>
      Promise.resolve({
        authorize_url: `https://cloud.ouraring.com/oauth/authorize?state=B&code_challenge=${ch}&code_challenge_method=S256`,
      }),
    );
    window.history.replaceState(null, "", "/settings/oura-callback?code=CA&state=A");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.stillFinishing)).toBeTruthy();

    fireEvent.click(screen.getByText("Retry")); // A's request is now outstanding
    await waitFor(() => expect(oura.completeOuraAuthorization).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByText("Start over"));
    fireEvent.click(await screen.findByText("Connect Oura"));
    await waitFor(() => expect(loadAttempt()?.state).toBe("B"));
    const connectionReads = oura.getWearableConnection.mock.calls.length;

    await act(async () => {
      replyA(SUCCEEDED);
      await Promise.resolve();
    });
    expect(loadAttempt()?.state).toBe("B");
    expect(screen.queryByText(OURA_MESSAGES.connected)).toBeNull();
    expect(oura.getWearableConnection.mock.calls.length).toBe(connectionReads);
  });
});

describe("the legacy return markers", () => {
  it("keeps the legacy error message under StrictMode", async () => {
    window.history.replaceState(null, "", "/settings?oura=error");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.legacyError)).toBeTruthy();
    expect(window.location.search).toBe("");
  });

  it("says a pre-cutover consent was interrupted by the update (crossing 1)", async () => {
    stored("S1");
    window.history.replaceState(null, "", "/settings?oura=error&reason=interrupted");
    renderStrict();
    expect(await screen.findByText(OURA_UPDATE_INTERRUPTED_MESSAGE)).toBeTruthy();
    expect(window.location.search).toBe("");
    expect(oura.completeOuraAuthorization).not.toHaveBeenCalled();
  });

  it("leaves an ordinary Settings visit alone", async () => {
    stored("S1");
    window.history.replaceState(null, "", "/settings");
    renderStrict();
    await waitFor(() => expect(oura.getWearableConnection).toHaveBeenCalled());
    expect(oura.completeOuraAuthorization).not.toHaveBeenCalled();
    expect(loadAttempt()?.state).toBe("S1");
  });
});

// P6b review (2026-10-14): the inputs go only on an outcome the tab recognizes, and a reply acts
// only for the operation and the account that asked.
describe("replies the tab can't be sure of, or that are no longer its own", () => {
  it.each([
    ["a success without its receipt", { status: "succeeded" }],
    ["a failure without a reason", { status: "failed", reason: null }],
  ])("keeps the inputs after %s", async (_name, reply) => {
    stored("S1");
    oura.completeOuraAuthorization.mockResolvedValue(reply);
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    renderStrict();
    await waitFor(() => expect(oura.completeOuraAuthorization).toHaveBeenCalledTimes(1));
    await act(async () => { await Promise.resolve(); });
    expect(loadAttempt()?.state).toBe("S1");
    expect(await screen.findByText(OURA_MESSAGES.stillFinishing)).toBeTruthy();
    expect(screen.getByText("Retry")).toBeTruthy();
  });

  it("keeps the inputs after a status it doesn't recognize (429)", async () => {
    stored("S1");
    oura.completeOuraAuthorization.mockRejectedValue(apiError(429));
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    renderStrict();
    await waitFor(() => expect(oura.completeOuraAuthorization).toHaveBeenCalledTimes(1));
    await act(async () => { await Promise.resolve(); });
    expect(loadAttempt()?.state).toBe("S1");
    expect(await screen.findByText("Retry")).toBeTruthy();
  });

  it("drops an authorize reply that arrives after another account signed in", async () => {
    let replyAuthorize!: () => void;
    oura.getOuraAuthorizeUrl.mockImplementation((_token: string, challenge: string) =>
      new Promise((resolve) => {
        replyAuthorize = () => resolve({
          authorize_url: `https://cloud.ouraring.com/oauth/authorize?state=A&code_challenge=${challenge}&code_challenge_method=S256`,
        });
      }),
    );
    window.history.replaceState(null, "", "/settings");
    const ui = renderStrict();
    fireEvent.click(await screen.findByText("Connect Oura"));
    await waitFor(() => expect(oura.getOuraAuthorizeUrl).toHaveBeenCalledTimes(1));
    auth = { token: "t2", email: "other@example.com" };
    ui.rerender(<React.StrictMode><SettingsScreen /></React.StrictMode>);
    await act(async () => { replyAuthorize(); await Promise.resolve(); });
    expect(assign).not.toHaveBeenCalled();
    expect(loadAttempt()).toBeNull();
  });

  it.each([["answers", true], ["fails", false]])("drops A's late connection read that %s once a newer attempt B has started", async (_how, answers) => {
    stored("A");
    let replyConnection!: () => void;
    oura.completeOuraAuthorization.mockResolvedValue(SUCCEEDED);
    oura.getWearableConnection
      .mockResolvedValueOnce(NOT_CONNECTED)
      .mockResolvedValueOnce(NOT_CONNECTED)
      .mockImplementation(() => new Promise((resolve, reject) => {
        replyConnection = () => (answers ? resolve(CONNECTED) : reject(new Error("lost")));
      }));
    oura.getOuraAuthorizeUrl.mockImplementation((_token: string, challenge: string) => Promise.resolve({
      authorize_url: `https://cloud.ouraring.com/oauth/authorize?state=B&code_challenge=${challenge}&code_challenge_method=S256`,
    }));
    window.history.replaceState(null, "", "/settings/oura-callback?code=CA&state=A");
    renderStrict();
    await waitFor(() => expect(oura.getWearableConnection).toHaveBeenCalledTimes(3));
    expect(loadAttempt()).toBeNull();
    fireEvent.click(await screen.findByText("Connect Oura"));
    await waitFor(() => expect(loadAttempt()?.state).toBe("B"));
    await act(async () => { replyConnection(); await Promise.resolve(); });
    expect(loadAttempt()?.state).toBe("B");
    expect(screen.queryByText(OURA_MESSAGES.connected)).toBeNull();
    expect(screen.queryByText(OURA_MESSAGES.linkedStatusUnknown)).toBeNull();
    expect(screen.queryByText(/not synced yet/)).toBeNull(); // the card wasn't repainted either
  });

  it("never sends the athlete to Oura when the verifier can't be stored", async () => {
    oura.getOuraAuthorizeUrl.mockImplementation((_token: string, challenge: string) => Promise.resolve({
      authorize_url: `https://cloud.ouraring.com/oauth/authorize?state=S1&code_challenge=${challenge}&code_challenge_method=S256`,
    }));
    window.history.replaceState(null, "", "/settings");
    renderStrict();
    // The jsdom storage object's own prototype: the global `Storage` may be another realm's.
    const proto = Object.getPrototypeOf(window.sessionStorage) as Storage;
    const blocked = vi.spyOn(proto, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });
    try {
      fireEvent.click(await screen.findByText("Connect Oura"));
      await waitFor(() => expect(oura.getOuraAuthorizeUrl).toHaveBeenCalledTimes(1));
      expect(await screen.findByText(OURA_MESSAGES.storageBlocked)).toBeTruthy();
      expect(assign).not.toHaveBeenCalled();
      expect(loadAttempt()).toBeNull();
    } finally {
      blocked.mockRestore();
    }
  });

  it("still finishes the return when storage refuses to keep its code, and Retry resends it", async () => {
    stored("S1");
    const proto = Object.getPrototypeOf(window.sessionStorage) as Storage;
    const blocked = vi.spyOn(proto, "setItem").mockImplementation(() => {
      throw new DOMException("storage full", "QuotaExceededError");
    });
    oura.completeOuraAuthorization
      .mockRejectedValueOnce(apiError(503, { status: "unknown", reason: "outcome_unknown" }))
      .mockResolvedValue(SUCCEEDED);
    oura.getWearableConnection.mockResolvedValueOnce(NOT_CONNECTED).mockResolvedValue(CONNECTED);
    try {
      window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
      renderStrict();
      expect(await screen.findByText(OURA_MESSAGES.stillFinishing)).toBeTruthy();
      expect(loadAttempt()?.callback).toBeUndefined(); // storage really kept no code
      fireEvent.click(await screen.findByText("Retry"));
      expect(await screen.findByText(OURA_MESSAGES.connected)).toBeTruthy();
      expect(oura.completeOuraAuthorization).toHaveBeenCalledTimes(2);
      expect(oura.completeOuraAuthorization).toHaveBeenLastCalledWith(
        { state: "S1", code: "C1", code_verifier: VERIFIER }, "t1",
      );
    } finally {
      blocked.mockRestore();
    }
  });
});

describe("late replies, other accounts, leaving Settings (review, 2026-10-14)", () => {
  it("keeps the inputs when a success carries an unreadable time", async () => {
    stored("S1");
    oura.completeOuraAuthorization.mockResolvedValue({ ...SUCCEEDED, completed_at: "not-a-time" });
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.stillFinishing)).toBeTruthy();
    expect(loadAttempt()?.state).toBe("S1");
    expect(screen.getByText("Retry")).toBeTruthy();
  });

  it("keeps the inputs when a success carries February 30", async () => {
    stored("S1");
    oura.completeOuraAuthorization.mockResolvedValue({ ...SUCCEEDED, completed_at: "2026-02-30T10:00:00" });
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    renderStrict();
    expect(await screen.findByText(OURA_MESSAGES.stillFinishing)).toBeTruthy();
    expect(loadAttempt()?.state).toBe("S1");
    expect(screen.getByText("Retry")).toBeTruthy();
  });

  it("drops an authorize reply that arrives after Settings closed", async () => {
    let replyAuthorize!: () => void;
    oura.getOuraAuthorizeUrl.mockImplementation((_t: string, challenge: string) => new Promise((resolve) => {
      replyAuthorize = () => resolve({
        authorize_url: `https://cloud.ouraring.com/oauth/authorize?state=A&code_challenge=${challenge}&code_challenge_method=S256`,
      });
    }));
    window.history.replaceState(null, "", "/settings");
    const ui = renderStrict();
    fireEvent.click(await screen.findByText("Connect Oura"));
    await waitFor(() => expect(oura.getOuraAuthorizeUrl).toHaveBeenCalledTimes(1));
    ui.unmount();
    window.history.replaceState(null, "", "/planning");
    await act(async () => { replyAuthorize(); await Promise.resolve(); });
    expect(assign).not.toHaveBeenCalled();
    expect(loadAttempt()).toBeNull();
  });

  it("does not authorize with the old token when the account changes during the PKCE digest", async () => {
    let finishDigest!: (value: ArrayBuffer) => void;
    const slowDigest = vi.spyOn(crypto.subtle, "digest").mockImplementation(() => new Promise((resolve) => {
      finishDigest = resolve;
    }));
    oura.getOuraAuthorizeUrl.mockImplementation((_t: string, challenge: string) => Promise.resolve({
      authorize_url: `https://cloud.ouraring.com/oauth/authorize?state=A&code_challenge=${challenge}&code_challenge_method=S256`,
    }));
    try {
      window.history.replaceState(null, "", "/settings");
      const ui = renderStrict();
      fireEvent.click(await screen.findByText("Connect Oura"));
      await waitFor(() => expect(slowDigest).toHaveBeenCalledTimes(1));
      auth = { token: "t2", email: "other@example.com" };
      ui.rerender(<React.StrictMode><SettingsScreen /></React.StrictMode>);
      await act(async () => { finishDigest(new ArrayBuffer(32)); await Promise.resolve(); });
      expect(oura.getOuraAuthorizeUrl).not.toHaveBeenCalled();
      expect(assign).not.toHaveBeenCalled();
    } finally {
      slowDigest.mockRestore();
    }
  });

  it("does not let account A's late connection reads repaint account B's card", async () => {
    const oldReads: Array<(value: unknown) => void> = [];
    oura.getWearableConnection.mockImplementation((token: string) => token === "t1"
      ? new Promise((resolve) => { oldReads.push(resolve); })
      : Promise.resolve(NOT_CONNECTED));
    window.history.replaceState(null, "", "/settings");
    const ui = renderStrict();
    await waitFor(() => expect(oldReads.length).toBeGreaterThan(0));
    auth = { token: "t2", email: "other@example.com" };
    ui.rerender(<React.StrictMode><SettingsScreen /></React.StrictMode>);
    expect(await screen.findByText("Connect Oura")).toBeTruthy();
    await act(async () => { oldReads.forEach((resolve) => resolve(CONNECTED)); await Promise.resolve(); });
    expect(screen.queryByText("Connected")).toBeNull();
    expect(screen.getByText("Connect Oura")).toBeTruthy();
  });

  it("lets only the latest read paint the card, within one account", async () => {
    const reads: Array<(value: unknown) => void> = [];
    oura.getWearableConnection.mockImplementation(() => new Promise((resolve) => { reads.push(resolve); }));
    oura.syncOura.mockResolvedValue({ rows_written: 0 });
    window.history.replaceState(null, "", "/settings");
    renderStrict();
    await waitFor(() => expect(reads.length).toBeGreaterThan(0));
    const initial = reads.length;
    // A newer read (here, the card reloading for a fresh token) supersedes the first ones.
    await act(async () => { reads[initial - 1](NOT_CONNECTED); await Promise.resolve(); });
    expect(await screen.findByText("Connect Oura")).toBeTruthy();
    await act(async () => { reads.slice(0, initial - 1).forEach((r) => r(CONNECTED)); await Promise.resolve(); });
    expect(screen.queryByText("Connected")).toBeNull();
  });
});

describe("every card writer is fenced (review, 2026-10-14)", () => {
  it("an action begun for account A never reports on account B's card", async () => {
    let finishSync!: (value: unknown) => void;
    oura.getWearableConnection.mockImplementation((token: string) =>
      Promise.resolve(token === "t1" ? CONNECTED : NOT_CONNECTED));
    oura.syncOura.mockImplementation(() => new Promise((resolve) => { finishSync = resolve; }));
    window.history.replaceState(null, "", "/settings");
    const ui = renderStrict();
    fireEvent.click(await screen.findByText("Sync now"));
    await waitFor(() => expect(oura.syncOura).toHaveBeenCalledTimes(1));
    auth = { token: "t2", email: "other@example.com" };
    ui.rerender(<React.StrictMode><SettingsScreen /></React.StrictMode>);
    expect(await screen.findByText("Connect Oura")).toBeTruthy();
    await act(async () => { finishSync({ rows_written: 3 }); await Promise.resolve(); });
    expect(screen.queryByText(/Synced 3 days/)).toBeNull();
  });

  it("the attempt's read settles the card even when it fails (third round)", async () => {
    stored("S1");
    const initial: Array<(value: unknown) => void> = [];
    let failHookRead: ((e: unknown) => void) | undefined;
    let replyComplete!: (value: unknown) => void;
    oura.getWearableConnection.mockImplementation(() => new Promise((resolve) => { initial.push(resolve); }));
    oura.completeOuraAuthorization.mockImplementation(() => new Promise((resolve) => { replyComplete = resolve; }));
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    renderStrict();
    await waitFor(() => expect(oura.completeOuraAuthorization).toHaveBeenCalled());
    await waitFor(() => expect(initial.length).toBeGreaterThan(0));
    expect(screen.getByText("Loading…")).toBeTruthy();
    // The attempt's read starts after the initial ones, so it is the newest; it fails.
    oura.getWearableConnection.mockImplementation(() => new Promise((_r, reject) => { failHookRead = reject; }));
    await act(async () => { replyComplete(SUCCEEDED); await Promise.resolve(); });
    await waitFor(() => expect(failHookRead).toBeDefined());
    expect(screen.getByText("Loading…")).toBeTruthy();
    await act(async () => { failHookRead?.({ message: "HTTP 500", status: 500 }); await Promise.resolve(); });
    expect(await screen.findByText(OURA_MESSAGES.linkedStatusUnknown)).toBeTruthy();
    // The older initial reads finish now; they may not paint, and nothing is left loading.
    await act(async () => { initial.forEach((r) => r(CONNECTED)); await Promise.resolve(); });
    expect(screen.queryByText("Loading…")).toBeNull();
    expect(screen.queryByText("Connected")).toBeNull();
    expect(screen.getByText("Connect Oura")).toBeTruthy();
  });

  it("a superseded attempt read can't end a newer read's loading (third round)", async () => {
    stored("S1");
    let replyComplete!: (value: unknown) => void;
    oura.completeOuraAuthorization.mockImplementation(() => new Promise((resolve) => { replyComplete = resolve; }));
    oura.getWearableConnection.mockResolvedValue(NOT_CONNECTED);
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    const ui = renderStrict();
    expect(await screen.findByText("Connect Oura")).toBeTruthy();
    let failHookRead: ((e: unknown) => void) | undefined;
    oura.getWearableConnection.mockImplementationOnce(() => new Promise((_r, reject) => { failHookRead = reject; }));
    await act(async () => { replyComplete(SUCCEEDED); await Promise.resolve(); });
    await waitFor(() => expect(failHookRead).toBeDefined());
    // A newer card read starts and is held: the card is loading for it.
    let replyNewer: ((value: unknown) => void) | undefined;
    oura.getWearableConnection.mockImplementation(() => new Promise((resolve) => { replyNewer = resolve; }));
    auth = { token: "t1-refreshed", email: EMAIL };
    ui.rerender(<React.StrictMode><SettingsScreen /></React.StrictMode>);
    expect(await screen.findByText("Loading…")).toBeTruthy();
    await act(async () => { failHookRead?.({ message: "HTTP 500", status: 500 }); await Promise.resolve(); });
    expect(screen.getByText("Loading…")).toBeTruthy(); // the older read may not end it
    await act(async () => { replyNewer?.(CONNECTED); await Promise.resolve(); });
    expect(screen.getByText("Connected")).toBeTruthy();
  });

  it("the attempt's own read loses to a newer card read, and then says nothing", async () => {
    stored("S1");
    let replyComplete!: (value: unknown) => void;
    oura.completeOuraAuthorization.mockImplementation(() => new Promise((resolve) => { replyComplete = resolve; }));
    oura.getWearableConnection.mockResolvedValue(NOT_CONNECTED);
    window.history.replaceState(null, "", "/settings/oura-callback?code=C1&state=S1");
    const ui = renderStrict();
    await waitFor(() => expect(oura.completeOuraAuthorization).toHaveBeenCalled());
    expect(await screen.findByText("Connect Oura")).toBeTruthy(); // the card's own loads have landed
    // From here the next read is the attempt's (after the success); hold it.
    let replyHookRead: ((value: unknown) => void) | undefined;
    oura.getWearableConnection.mockImplementationOnce(() => new Promise((resolve) => { replyHookRead = resolve; }));
    await act(async () => { replyComplete(SUCCEEDED); await Promise.resolve(); });
    await waitFor(() => expect(replyHookRead).toBeDefined());
    // A newer read: the same athlete's token was refreshed, so the card reloads and lands first.
    auth = { token: "t1-refreshed", email: EMAIL };
    ui.rerender(<React.StrictMode><SettingsScreen /></React.StrictMode>);
    await waitFor(() => expect(oura.getWearableConnection).toHaveBeenCalledWith("t1-refreshed"));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("Connect Oura")).toBeTruthy();
    await act(async () => { replyHookRead?.(CONNECTED); await Promise.resolve(); });
    expect(screen.queryByText("Connected")).toBeNull();
    expect(screen.queryByText(OURA_MESSAGES.connected)).toBeNull();
  });

  it("the hook alone drops an authorize reply once another account is signed in", async () => {
    let reply!: () => void;
    oura.getOuraAuthorizeUrl.mockImplementation((_t: string, challenge: string) => new Promise((resolve) => {
      reply = () => resolve({
        authorize_url: `https://cloud.ouraring.com/oauth/authorize?state=A&code_challenge=${challenge}&code_challenge_method=S256`,
      });
    }));
    const props = {
      token: "t1" as string | null, userKey: EMAIL,
      readConnection: vi.fn(), showConnection: vi.fn(() => true),
    };
    const { result, rerender } = renderHook((p: typeof props) => useOuraAttempt(p), { initialProps: props });
    let pending!: Promise<void>;
    act(() => { pending = result.current.connect(); });
    await waitFor(() => expect(oura.getOuraAuthorizeUrl).toHaveBeenCalledTimes(1));
    rerender({ ...props, token: "t2", userKey: "other@example.com" });
    await act(async () => { reply(); await pending; });
    expect(assign).not.toHaveBeenCalled();
    expect(loadAttempt()).toBeNull();
  });
});
