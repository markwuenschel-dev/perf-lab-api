// @vitest-environment jsdom
//
// The seam for the Oura callback (ADR-0075), end to end through the real provider and AppShell:
// a return to /settings/oura-callback must land on Settings, where the attempt is finished or
// explained. Landing anywhere else would show nothing, which no test of the card alone sees.
// Signed out here, so the stored attempt is kept and the athlete is asked to sign in.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token: null, user: null, profile: null, email: "", isGuest: true }),
}));

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const { PerfLabProvider } = await import("./PerfLabProvider");
const { AppShell } = await import("./AppShell");
const { resetOuraReturnForTests } = await import("./wearableSync");
const { loadAttempt, saveAttempt } = await import("./ouraPkce");
const { OURA_MESSAGES } = await import("./screens/useOuraAttempt");

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

describe("an Oura return to the callback path", () => {
  it("opens Settings, keeps the attempt, and asks to sign in to finish", async () => {
    resetOuraReturnForTests();
    saveAttempt({ state: "s", verifier: "v".repeat(64), challenge: "c".repeat(43), startedAt: 1, userKey: "athlete@example.com" });
    window.history.replaceState(null, "", "/settings/oura-callback?code=c&state=s");
    render(
      <PerfLabProvider>
        <AppShell />
      </PerfLabProvider>,
    );
    await waitFor(() => expect(screen.getByText(OURA_MESSAGES.signIn)).toBeTruthy());
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeTruthy();
    expect(window.location.pathname + window.location.search).toBe("/settings");
    expect(loadAttempt()?.callback).toEqual({ code: "c", error: undefined });
  });
});
