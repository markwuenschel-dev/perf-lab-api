// @vitest-environment jsdom
//
// B1: the session bootstrap offers the browser's timezone only while the athlete's is unknown,
// through the REAL <AuthProvider>. A known zone is never offered (a trip or a second device
// must not replace it), and a failed capture leaves the session intact.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "./AuthContext";
import { useAuth } from "./useAuth";

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

  it("keeps the session and the unknown zone when the capture fails", async () => {
    getProfile.mockResolvedValue({ timezone: null, timezone_source: null });
    captureDetectedTimezone.mockRejectedValue({ message: "HTTP 422", status: 422 });
    mount();
    await waitFor(() => expect(captureDetectedTimezone).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("zone").textContent).toBe("null");
    expect(sessionStorage.getItem("perf_lab_access_token")).toBe(TOKEN);
  });
});
