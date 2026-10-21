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

function Probe() {
  const auth = useAuth();
  const res = useAuthedResource((t) => readToday(t), [], { calendar: true });
  return (
    <div>
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
