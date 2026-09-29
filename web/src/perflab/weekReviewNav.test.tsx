// @vitest-environment jsdom
//
// The seam, end to end: the real provider, the real sidebar and the real AppShell
// routing table. Clicking "Week review" must land on the Week review screen — a nav
// entry with no route would silently fall back to Overview (`SCREENS[screen] ??
// OverviewScreen`), which no unit test of either side would notice.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token: null, user: null, profile: null, email: "", isGuest: true }),
}));

// The viz Chart sizes itself with ResizeObserver, which jsdom does not provide.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const { PerfLabProvider } = await import("./PerfLabProvider");
const { AppShell } = await import("./AppShell");

afterEach(cleanup);

describe("Week review nav item", () => {
  it("sits after Planning and routes to the Week review screen", () => {
    render(
      <PerfLabProvider>
        <AppShell />
      </PerfLabProvider>,
    );
    const labels = Array.from(document.querySelectorAll("nav .nav-label")).map((n) => n.textContent);
    expect(labels.indexOf("Week review")).toBe(labels.indexOf("Planning") + 1);

    expect(screen.queryByRole("heading", { level: 1, name: "Week review" })).toBeNull();
    fireEvent.click(screen.getByText("Week review", { selector: ".nav-label" }));
    expect(screen.getByRole("heading", { level: 1, name: "Week review" })).toBeTruthy();
  });
});
