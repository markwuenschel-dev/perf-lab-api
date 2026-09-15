// @vitest-environment jsdom
//
// What happens to a signed-in athlete when GET /auth/me fails.
//
// The classifier's decision table is proven in authFailure.test.ts. What can
// ONLY be proven by mounting the real <AuthProvider> is the consequence:
// whether `perf_lab_access_token` is still in sessionStorage afterwards. The
// old code ran `catch { logout() }` untyped, so every deploy, container restart
// or proxy blip evicted every open tab. These tests pin the storage keys
// themselves — the thing the athlete actually loses.
//
// The client module is mocked wholesale rather than stubbing global fetch,
// because perfLabClient throws at call time when VITE_API_BASE_URL is unset
// (src/api/perfLabClient.ts:57-63).
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "./AuthContext";
import { useAuth } from "./useAuth";
import type { AuthContextValue } from "./perfLabAuthContext";

const fetchMe = vi.fn();
const getProfile = vi.fn();
const onboard = vi.fn();

vi.mock("@/api/perfLabClient", () => ({
  fetchMe: (...args: unknown[]) => fetchMe(...args),
  getProfile: (...args: unknown[]) => getProfile(...args),
  login: vi.fn(),
  register: vi.fn(),
  onboard: (...args: unknown[]) => onboard(...args),
}));

const TOKEN_KEY = "perf_lab_access_token";
const EMAIL_KEY = "perf_lab_session_email";
const TOKEN = "stored-token";

const SESSION_USER = { id: 7, email: "athlete@example.com", is_active: true };

/** The exact object shape perfLabClient throws (src/api/perfLabClient.ts:116-121). */
function apiError(status: number) {
  return { message: `HTTP ${status}`, status, details: undefined };
}

let latest: AuthContextValue | null = null;

function Probe() {
  const auth = useAuth();
  // Captured after commit (never during render) so the imperative bits —
  // retrySession(), logout() — are reachable from the test body.
  useEffect(() => {
    latest = auth;
  });
  return (
    <div>
      <span data-testid="authed">{String(auth.isAuthenticated)}</span>
      <span data-testid="user">{auth.user?.email ?? "-"}</span>
      <span data-testid="reason">{auth.sessionUnavailable?.reason ?? "-"}</span>
    </div>
  );
}

/** Mounts the REAL provider over a pre-seeded session, exactly like a reload. */
function renderWithStoredSession() {
  sessionStorage.setItem(TOKEN_KEY, TOKEN);
  sessionStorage.setItem(EMAIL_KEY, "athlete@example.com");
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
}

function storedToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

/**
 * Flush the mocked call + its state update. Asserted BEFORE the reason-code
 * `waitFor` so that a regression fails on the storage keys themselves — the
 * thing the athlete loses — rather than timing out on a state that never comes.
 */
async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  sessionStorage.clear();
  latest = null;
  fetchMe.mockReset();
  getProfile.mockReset();
  getProfile.mockResolvedValue(null);
  onboard.mockReset();
});

afterEach(cleanup);

describe("a 401 is the one failure that signs the athlete out", () => {
  it("clears both session keys", async () => {
    fetchMe.mockRejectedValue(apiError(401));
    renderWithStoredSession();

    await waitFor(() => expect(screen.getByTestId("authed").textContent).toBe("false"));
    expect(storedToken()).toBeNull();
    expect(sessionStorage.getItem(EMAIL_KEY)).toBeNull();
  });

  it("does not raise the retryable unavailable state — this is a real logout", async () => {
    fetchMe.mockRejectedValue(apiError(401));
    renderWithStoredSession();

    await waitFor(() => expect(storedToken()).toBeNull());
    expect(screen.getByTestId("reason").textContent).toBe("-");
  });
});

describe("a transient failure MUST NOT evict the athlete", () => {
  it.each([
    [500, "server-error"],
    [502, "server-error"],
    [503, "server-error"],
    [504, "server-error"],
  ])("keeps the token on %i and reports %s", async (status, reason) => {
    fetchMe.mockRejectedValue(apiError(status));
    renderWithStoredSession();
    await settle();

    expect(storedToken()).toBe(TOKEN);
    expect(sessionStorage.getItem(EMAIL_KEY)).toBe("athlete@example.com");
    expect(screen.getByTestId("authed").textContent).toBe("true");
    await waitFor(() => expect(screen.getByTestId("reason").textContent).toBe(reason));
    expect(latest?.sessionUnavailable?.status).toBe(status);
  });

  it.each([403, 404, 429])("keeps the token on a non-401 4xx (%i)", async (status) => {
    fetchMe.mockRejectedValue(apiError(status));
    renderWithStoredSession();
    await settle();

    expect(storedToken()).toBe(TOKEN);
    expect(screen.getByTestId("authed").textContent).toBe("true");
    await waitFor(() =>
      expect(screen.getByTestId("reason").textContent).toBe("request-rejected"),
    );
  });

  it("keeps the token when fetch itself rejects (offline / CORS / DNS)", async () => {
    // A bare `await fetch(...)` rejection never reaches handleResponse, so it
    // arrives as a TypeError carrying no `status` at all.
    fetchMe.mockRejectedValue(new TypeError("Failed to fetch"));
    renderWithStoredSession();
    await settle();

    expect(storedToken()).toBe(TOKEN);
    expect(screen.getByTestId("authed").textContent).toBe("true");
    await waitFor(() => expect(screen.getByTestId("reason").textContent).toBe("network"));
    expect(latest?.sessionUnavailable?.status).toBeNull();
  });

  it("keeps the token when /auth/me resolves with a payload that is not a session", async () => {
    // e.g. a proxy 200 carrying an HTML interstitial.
    fetchMe.mockResolvedValue("<html>Gateway</html>");
    renderWithStoredSession();
    await settle();

    expect(storedToken()).toBe(TOKEN);
    expect(screen.getByTestId("user").textContent).toBe("-");
    expect(screen.getByTestId("authed").textContent).toBe("true");
    await waitFor(() =>
      expect(screen.getByTestId("reason").textContent).toBe("malformed-response"),
    );
  });

  it("keeps the token for an error shape nobody anticipated", async () => {
    fetchMe.mockRejectedValue("something went sideways");
    renderWithStoredSession();
    await settle();

    expect(storedToken()).toBe(TOKEN);
    await waitFor(() => expect(screen.getByTestId("reason").textContent).toBe("unknown"));
  });
});

describe("retry", () => {
  it("re-attempts the session check and clears the unavailable state on success", async () => {
    fetchMe.mockRejectedValueOnce(apiError(503)).mockResolvedValueOnce(SESSION_USER);
    renderWithStoredSession();
    await settle();
    expect(storedToken()).toBe(TOKEN);

    await waitFor(() =>
      expect(screen.getByTestId("reason").textContent).toBe("server-error"),
    );

    await act(async () => {
      await latest!.retrySession();
    });

    expect(fetchMe).toHaveBeenCalledTimes(2);
    expect(fetchMe).toHaveBeenLastCalledWith(TOKEN);
    expect(screen.getByTestId("reason").textContent).toBe("-");
    expect(screen.getByTestId("user").textContent).toBe("athlete@example.com");
    expect(storedToken()).toBe(TOKEN);
  });

  it("leaves the unavailable state up when the retry also fails", async () => {
    fetchMe.mockRejectedValue(apiError(503));
    renderWithStoredSession();
    await settle();
    expect(storedToken()).toBe(TOKEN);

    await waitFor(() =>
      expect(screen.getByTestId("reason").textContent).toBe("server-error"),
    );

    await act(async () => {
      await latest!.retrySession();
    });

    expect(screen.getByTestId("reason").textContent).toBe("server-error");
    expect(storedToken()).toBe(TOKEN);
  });
});

describe("the healthy path is unchanged", () => {
  it("loads the user and the profile, with no unavailable state", async () => {
    fetchMe.mockResolvedValue(SESSION_USER);
    getProfile.mockResolvedValue({ first_name: "Sam" });
    renderWithStoredSession();

    await waitFor(() =>
      expect(screen.getByTestId("user").textContent).toBe("athlete@example.com"),
    );
    await waitFor(() => expect(latest?.profile).toEqual({ first_name: "Sam" }));
    expect(screen.getByTestId("reason").textContent).toBe("-");
    expect(storedToken()).toBe(TOKEN);
  });

  it("a failing profile load stays best-effort — it is not a session failure", async () => {
    fetchMe.mockResolvedValue(SESSION_USER);
    getProfile.mockRejectedValue(apiError(500));
    renderWithStoredSession();

    await waitFor(() =>
      expect(screen.getByTestId("user").textContent).toBe("athlete@example.com"),
    );
    expect(screen.getByTestId("reason").textContent).toBe("-");
    expect(latest?.profile).toBeNull();
    expect(storedToken()).toBe(TOKEN);
  });

  it("explicit logout still clears the session", async () => {
    fetchMe.mockResolvedValue(SESSION_USER);
    renderWithStoredSession();
    await waitFor(() => expect(screen.getByTestId("authed").textContent).toBe("true"));

    await act(async () => {
      latest!.logout();
    });

    expect(storedToken()).toBeNull();
    expect(sessionStorage.getItem(EMAIL_KEY)).toBeNull();
    expect(screen.getByTestId("authed").textContent).toBe("false");
  });
});

describe("completeOnboarding sends the athlete's facts with their session", () => {
  const REQ = {
    goal: "Strength",
    strength: [
      {
        benchmark_code: "pl_e1rm_squat",
        method: "tested_max" as const,
        value_kg: 140,
        performed_at: "2026-09-10T00:00:00.000Z",
      },
    ],
  };

  it("forwards the stored bearer token to POST /v1/onboard", async () => {
    fetchMe.mockResolvedValue(SESSION_USER);
    onboard.mockResolvedValue({});
    renderWithStoredSession();
    await waitFor(() =>
      expect(screen.getByTestId("user").textContent).toBe("athlete@example.com"),
    );

    await act(async () => {
      await latest!.completeOnboarding(REQ);
    });

    expect(onboard).toHaveBeenCalledTimes(1);
    expect(onboard).toHaveBeenCalledWith(REQ, TOKEN);
  });

  it("lets a refused onboarding reach the screen instead of reporting success", async () => {
    fetchMe.mockResolvedValue(SESSION_USER);
    onboard.mockRejectedValue(apiError(422));
    renderWithStoredSession();
    await waitFor(() =>
      expect(screen.getByTestId("user").textContent).toBe("athlete@example.com"),
    );

    let caught: unknown = null;
    await act(async () => {
      await latest!.completeOnboarding(REQ).catch((e: unknown) => {
        caught = e;
      });
    });

    expect(caught).toEqual(apiError(422));
    // A refusal is not a session failure: the athlete keeps the session to retry with.
    expect(storedToken()).toBe(TOKEN);
  });
});
