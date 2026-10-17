// @vitest-environment jsdom
//
// A production build (PREVIEW_TOOLS false) with an empty-state preview left in storage
// from a development session. The stored flag must be dropped before the FIRST render:
// a MutationObserver records every node the app ever adds, so an empty state that is
// painted and then removed by a later effect still fails here.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./previewTools", () => ({ PREVIEW_TOOLS: false }));

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
const { STORAGE_KEY } = await import("./store");

const EMPTY_TITLE = "Welcome to Perf Lab";

/** Node's own localStorage global shadows jsdom's and throws (see onboardingStrength.test.tsx). */
function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => {
      data.delete(key);
    },
    setItem: (key, value) => {
      data.set(key, String(value));
    },
  };
}

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("a production build with a stored empty-state preview", () => {
  it("never renders the empty state, hides the toggle, and rewrites storage", async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ fresh: true }));

    const added: string[] = [];
    const observer = new MutationObserver((records) => {
      for (const r of records) r.addedNodes.forEach((n) => added.push(n.textContent ?? ""));
    });
    observer.observe(document.body, { childList: true, subtree: true });

    render(
      <PerfLabProvider>
        <AppShell />
      </PerfLabProvider>,
    );
    await Promise.resolve(); // deliver the observer's records
    observer.disconnect();

    expect(added.length).toBeGreaterThan(0);
    expect(added.some((t) => t.includes(EMPTY_TITLE))).toBe(false);
    expect(screen.queryByText(EMPTY_TITLE)).toBeNull();
    expect(screen.queryByRole("button", { name: /empty-state preview|Preview empty state/ })).toBeNull();
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}").fresh).toBe(false);
  });
});
