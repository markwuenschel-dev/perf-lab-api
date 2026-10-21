// @vitest-environment jsdom
//
// B1: the calendar identity across a session's life, through the REAL <AuthProvider>.
//   - The profile settles the calendar; the zone capture is optional and never blocks data.
//   - A hung profile load times out to "unavailable" instead of leaving everything "pending".
//   - Retrying the session reaches the capture; a failed refresh keeps the confirmed zone.
//   - Logout, a 401 and a new login all start the calendar over.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "./AuthContext";
import { notifyUnauthorized } from "./sessionBridge";
import { useAuth } from "./useAuth";
import { useAuthedResource } from "../perflab/useAuthedResource";

const fetchMe = vi.fn();
const getProfile = vi.fn();
const captureDetectedTimezone = vi.fn();
const login = vi.fn();
const readToday = vi.fn();

vi.mock("@/api/perfLabClient", () => ({
  fetchMe: (...a: unknown[]) => fetchMe(...a),
  getProfile: (...a: unknown[]) => getProfile(...a),
  captureDetectedTimezone: (...a: unknown[]) => captureDetectedTimezone(...a),
  login: (...a: unknown[]) => login(...a),
  register: vi.fn(),
  onboard: vi.fn(),
}));

const TOKEN = "stored-token";
const USER = { id: 7, email: "athlete@example.com", is_active: true };
const never = () => new Promise<never>(() => undefined);

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

/** Every (token, calendarKey) pair the app rendered, in order. */
const rendered: [string | null, string][] = [];

function Probe() {
  const auth = useAuth();
  const res = useAuthedResource((t) => readToday(t), [], { calendar: true });
  rendered.push([auth.token, auth.calendarKey]);
  return (
    <div>
      <span data-testid="session">{auth.sessionUnavailable ? auth.sessionUnavailable.reason : "ok"}</span>
      <span data-testid="day">
        {auth.calendarKey}:{res.status === "success" ? String(res.data) : res.status}
      </span>
      <button onClick={() => void auth.retrySession()}>retry</button>
      <button onClick={() => void auth.refreshProfile()}>refresh</button>
      <button onClick={() => auth.logout()}>logout</button>
      <button onClick={() => void auth.login("athlete@example.com", "pw").catch(() => undefined)}>login</button>
    </div>
  );
}

function mount(profileTimeoutMs?: number) {
  sessionStorage.setItem("perf_lab_access_token", TOKEN);
  sessionStorage.setItem("perf_lab_session_email", USER.email);
  return render(
    <AuthProvider profileTimeoutMs={profileTimeoutMs}>
      <Probe />
    </AuthProvider>,
  );
}

const day = () => screen.getByTestId("day").textContent;

beforeEach(() => {
  fetchMe.mockResolvedValue(USER);
  vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
    timeZone: "America/New_York",
  } as Intl.ResolvedDateTimeFormatOptions);
});

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.restoreAllMocks();
  for (const f of [fetchMe, getProfile, captureDetectedTimezone, login, readToday]) f.mockReset();
  rendered.length = 0;
});

describe("the profile settles the calendar; the capture never blocks", () => {
  it("loads on the server's unknown zone while a capture hangs, and refetches once it lands", async () => {
    getProfile.mockResolvedValue({ timezone: null, timezone_source: null });
    const capture = deferred<unknown>();
    captureDetectedTimezone.mockReturnValue(capture.promise);
    readToday.mockResolvedValueOnce("UTC day").mockResolvedValueOnce("New York day");
    mount();
    await waitFor(() => expect(day()).toBe("unknown:UTC day"));
    expect(captureDetectedTimezone).toHaveBeenCalledTimes(1); // still in flight
    await act(async () => capture.resolve({ timezone: "America/New_York", timezone_source: "browser" }));
    await waitFor(() => expect(day()).toBe("zone:America/New_York:New York day"));
    expect(readToday).toHaveBeenCalledTimes(2);
  });

  it("is not blocked by a capture that never answers", async () => {
    getProfile.mockResolvedValue({ timezone: null, timezone_source: null });
    captureDetectedTimezone.mockReturnValue(never());
    readToday.mockResolvedValue("UTC day");
    mount();
    await waitFor(() => expect(day()).toBe("unknown:UTC day"));
  });
});

describe("a hung profile load", () => {
  it("times out to 'unavailable' instead of staying pending", async () => {
    getProfile.mockReturnValue(never());
    readToday.mockResolvedValue("server day");
    mount(30);
    expect(day()).toBe("pending:loading");
    await waitFor(() => expect(day()).toBe("unavailable:server day"));
    // The request itself was given an abort signal, so the timeout also cancels it.
    expect(getProfile.mock.calls[0][1]).toBeInstanceOf(AbortSignal);
    expect((getProfile.mock.calls[0][1] as AbortSignal).aborted).toBe(true);
  });
});

describe("recovering", () => {
  it("retrying the session after a failed profile load also captures the zone", async () => {
    getProfile.mockRejectedValueOnce({ message: "HTTP 503", status: 503 });
    readToday.mockResolvedValue("a day");
    mount();
    await waitFor(() => expect(day()).toBe("unavailable:a day"));
    getProfile.mockResolvedValueOnce({ timezone: null, timezone_source: null });
    captureDetectedTimezone.mockResolvedValueOnce({ timezone: "America/New_York", timezone_source: "browser" });
    fireEvent.click(screen.getByText("retry"));
    await waitFor(() => expect(captureDetectedTimezone).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(day()).toBe("zone:America/New_York:a day"));
  });

  it("a failed refresh keeps the last confirmed zone", async () => {
    getProfile.mockResolvedValueOnce({ timezone: "Europe/London", timezone_source: "athlete" });
    readToday.mockResolvedValue("London day");
    mount();
    await waitFor(() => expect(day()).toBe("zone:Europe/London:London day"));
    getProfile.mockRejectedValueOnce({ message: "HTTP 503", status: 503 });
    fireEvent.click(screen.getByText("refresh"));
    await waitFor(() => expect(getProfile).toHaveBeenCalledTimes(2));
    await act(async () => undefined);
    expect(day()).toBe("zone:Europe/London:London day");
    expect(readToday).toHaveBeenCalledTimes(1); // nothing was invalidated
  });
});

describe("the calendar starts over with the session", () => {
  async function established() {
    getProfile.mockResolvedValue({ timezone: "Europe/London", timezone_source: "athlete" });
    readToday.mockResolvedValue("London day");
    mount();
    await waitFor(() => expect(day()).toBe("zone:Europe/London:London day"));
  }

  it("on logout", async () => {
    await established();
    fireEvent.click(screen.getByText("logout"));
    await waitFor(() => expect(day()).toBe("pending:guest"));
  });

  it("on a 401", async () => {
    await established();
    await act(async () => notifyUnauthorized());
    expect(day()).toMatch(/^pending:/);
  });

  it("on a new login without a logout, never pairing the new token with the old calendar", async () => {
    await established();
    login.mockResolvedValue({ access_token: "token-b" });
    const nextProfile = deferred<unknown>();
    getProfile.mockReturnValue(nextProfile.promise);
    fireEvent.click(screen.getByText("login"));
    await waitFor(() => expect(getProfile).toHaveBeenCalledTimes(2));
    expect(day()).toBe("pending:loading");
    // At no render did athlete B's token stand next to athlete A's London calendar.
    expect(rendered.filter(([t, k]) => t === "token-b" && k === "zone:Europe/London")).toEqual([]);
    await act(async () => nextProfile.resolve({ timezone: "Asia/Tokyo", timezone_source: "athlete" }));
    await waitFor(() => expect(day()).toBe("zone:Asia/Tokyo:London day"));
  });

  it("on a new login, until the new session's profile answers", async () => {
    await established();
    fireEvent.click(screen.getByText("logout"));
    login.mockResolvedValue({ access_token: "new-token" });
    const nextProfile = deferred<unknown>();
    getProfile.mockReturnValue(nextProfile.promise);
    fireEvent.click(screen.getByText("login"));
    await waitFor(() => expect(getProfile).toHaveBeenCalledTimes(2));
    expect(day()).toBe("pending:loading");
    await act(async () => nextProfile.resolve({ timezone: "Asia/Tokyo", timezone_source: "athlete" }));
    await waitFor(() => expect(day()).toBe("zone:Asia/Tokyo:London day"));
  });
});


describe("late answers belong to the session that asked", () => {
  it("a retry and a refresh that resolve after another athlete logs in change nothing", async () => {
    const lateRetry = deferred<unknown>();
    const lateRefresh = deferred<unknown>();
    getProfile
      .mockResolvedValueOnce({ timezone: "Europe/London", timezone_source: "athlete" }) // A's load
      .mockReturnValueOnce(lateRetry.promise) // A's retry
      .mockReturnValueOnce(lateRefresh.promise) // A's refresh
      .mockResolvedValueOnce({ timezone: "Asia/Tokyo", timezone_source: "athlete" }); // B's load
    readToday.mockResolvedValue("a day");
    mount();
    await waitFor(() => expect(day()).toBe("zone:Europe/London:a day"));
    fireEvent.click(screen.getByText("retry"));
    fireEvent.click(screen.getByText("refresh"));
    await waitFor(() => expect(getProfile).toHaveBeenCalledTimes(3));

    fireEvent.click(screen.getByText("logout"));
    login.mockResolvedValue({ access_token: "token-b" });
    fireEvent.click(screen.getByText("login"));
    await waitFor(() => expect(day()).toBe("zone:Asia/Tokyo:a day"));

    // Athlete A's answers arrive now: they must not become athlete B's calendar.
    await act(async () => {
      lateRetry.resolve({ timezone: "America/Chicago", timezone_source: "athlete" });
      lateRefresh.resolve({ timezone: "America/Chicago", timezone_source: "athlete" });
    });
    await act(async () => undefined);
    expect(day()).toBe("zone:Asia/Tokyo:a day");
    expect(rendered.some(([, k]) => k === "zone:America/Chicago")).toBe(false);
  });
});

describe("a hung session check (/auth/me)", () => {
  it("times out to a retryable unavailable session; day data is not left loading", async () => {
    fetchMe.mockReturnValue(never());
    readToday.mockResolvedValue("server day");
    mount(30);
    expect(day()).toBe("pending:loading");
    // A timeout is a network failure: the token is kept and the check can be retried.
    await waitFor(() => expect(screen.getByTestId("session").textContent).toBe("network"));
    expect(sessionStorage.getItem("perf_lab_access_token")).toBe(TOKEN);
    expect((fetchMe.mock.calls[0][1] as AbortSignal).aborted).toBe(true);
    // No profile was read, so the calendar is "unavailable" (not pending): the day-dependent
    // read goes ahead and shows the server's view instead of a spinner that never ends.
    await waitFor(() => expect(day()).toBe("unavailable:server day"));
    expect(getProfile).not.toHaveBeenCalled();
  });
});
