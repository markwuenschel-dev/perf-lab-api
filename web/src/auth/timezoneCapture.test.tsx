// @vitest-environment jsdom
//
// B1: the session bootstrap offers the browser's timezone only while the athlete's is unknown,
// through the REAL <AuthProvider>. A known zone is never offered (a trip or a second device
// must not replace it), and a failed capture leaves the session intact.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "./AuthContext";
import { useAuth } from "./useAuth";
import { useAuthedResource } from "../perflab/useAuthedResource";

const fetchMe = vi.fn();
const getProfile = vi.fn();
const captureDetectedTimezone = vi.fn();

vi.mock("@/api/perfLabClient", () => ({
  fetchMe: (...args: unknown[]) => fetchMe(...args),
  getProfile: (...args: unknown[]) => getProfile(...args),
  captureDetectedTimezone: (...args: unknown[]) => captureDetectedTimezone(...args),
  login: vi.fn(),
  register: vi.fn(),
  onboard: vi.fn(),
}));

const TOKEN = "stored-token";
const USER = { id: 7, email: "athlete@example.com", is_active: true };

function Probe() {
  const auth = useAuth();
  return <span data-testid="zone">{auth.profile ? String(auth.profile.timezone) : "-"}</span>;
}

function mount() {
  sessionStorage.setItem("perf_lab_access_token", TOKEN);
  sessionStorage.setItem("perf_lab_session_email", USER.email);
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
}

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
  fetchMe.mockReset();
  getProfile.mockReset();
  captureDetectedTimezone.mockReset();
  readToday.mockReset();
});

const readToday = vi.fn();

/** A day-dependent resource, as a screen would hold one (e.g. readiness, today's session). */
function DayProbe() {
  const auth = useAuth();
  const res = useAuthedResource((t) => readToday(t), [], { calendar: true });
  return (
    <span data-testid="day">
      {auth.calendarKey}:{res.status === "success" ? String(res.data) : res.status}
    </span>
  );
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("day-dependent data follows the confirmed calendar", () => {
  it("does not hold day data for the capture: it loads on the profile, then follows the zone", async () => {
    getProfile.mockResolvedValueOnce({ timezone: null, timezone_source: null });
    const capture = deferred<unknown>();
    captureDetectedTimezone.mockReturnValue(capture.promise);
    readToday.mockResolvedValueOnce("UTC day").mockResolvedValueOnce("New York day");
    sessionStorage.setItem("perf_lab_access_token", TOKEN);
    sessionStorage.setItem("perf_lab_session_email", USER.email);
    render(
      <AuthProvider>
        <DayProbe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("day").textContent).toBe("unknown:UTC day"));
    capture.resolve({ timezone: "America/New_York", timezone_source: "browser" });
    await waitFor(() =>
      expect(screen.getByTestId("day").textContent).toBe("zone:America/New_York:New York day"),
    );
    expect(readToday).toHaveBeenCalledTimes(2);
  });

  it("shows the calendar as unavailable, not unknown, when the profile fails to load", async () => {
    getProfile.mockRejectedValueOnce({ message: "HTTP 503", status: 503 });
    readToday.mockResolvedValue("server day");
    sessionStorage.setItem("perf_lab_access_token", TOKEN);
    sessionStorage.setItem("perf_lab_session_email", USER.email);
    render(
      <AuthProvider>
        <DayProbe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("day").textContent).toBe("unavailable:server day"));
  });
});

describe("timezone capture on sign-in / app load", () => {
  it("offers the browser zone once when the athlete's is unknown", async () => {
    getProfile.mockResolvedValue({ timezone: null, timezone_source: null });
    captureDetectedTimezone.mockResolvedValue({ timezone: "America/New_York", timezone_source: "browser" });
    mount();
    await waitFor(() => expect(screen.getByTestId("zone").textContent).toBe("America/New_York"));
    expect(captureDetectedTimezone).toHaveBeenCalledTimes(1);
    expect(captureDetectedTimezone).toHaveBeenCalledWith({ timezone: "America/New_York" }, TOKEN);
  });

  it("never offers it when the athlete's zone is known", async () => {
    getProfile.mockResolvedValue({ timezone: "Europe/London", timezone_source: "athlete" });
    mount();
    await waitFor(() => expect(screen.getByTestId("zone").textContent).toBe("Europe/London"));
    expect(captureDetectedTimezone).not.toHaveBeenCalled();
  });

  it("survives an API without the capture route (404): session kept, zone stays unknown", async () => {
    // Mixed-version rollout: the route is missing (an API from before B1, or one rolled back).
    getProfile.mockResolvedValue({ timezone: null, timezone_source: null });
    captureDetectedTimezone.mockRejectedValue({ message: "HTTP 404", status: 404 });
    mount();
    await waitFor(() => expect(captureDetectedTimezone).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("zone").textContent).toBe("null");
    expect(sessionStorage.getItem("perf_lab_access_token")).toBe(TOKEN);
  });

  it("keeps the session and the unknown zone when the capture fails", async () => {
    getProfile.mockResolvedValue({ timezone: null, timezone_source: null });
    captureDetectedTimezone.mockRejectedValue({ message: "HTTP 422", status: 422 });
    mount();
    await waitFor(() => expect(captureDetectedTimezone).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("zone").textContent).toBe("null");
    expect(sessionStorage.getItem("perf_lab_access_token")).toBe(TOKEN);
  });
});
