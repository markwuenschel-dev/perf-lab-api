// @vitest-environment jsdom
//
// The seam for crossing 3 (ADR-0075), end to end through the real provider and AppShell: an Oura
// return to /settings/oura-callback must land on Settings, where the card says the attempt was
// interrupted. Landing anywhere else would show nothing, which no test of the card alone sees.
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
const { OURA_INTERRUPTED_MESSAGE, resetOuraReturnForTests } = await import("./wearableSync");

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("an Oura return to the PKCE callback path", () => {
  it("opens Settings and says the attempt was interrupted", async () => {
    resetOuraReturnForTests();
    window.history.replaceState(null, "", "/settings/oura-callback?code=c&state=s");
    render(
      <PerfLabProvider>
        <AppShell />
      </PerfLabProvider>,
    );
    await waitFor(() => expect(screen.getByText(OURA_INTERRUPTED_MESSAGE)).toBeTruthy());
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeTruthy();
    expect(window.location.pathname + window.location.search).toBe("/settings");
  });
});
