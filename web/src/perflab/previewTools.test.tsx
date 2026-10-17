// @vitest-environment jsdom
//
// The empty-state preview is a development tool (P8). This file runs in the test build,
// where PREVIEW_TOOLS is the real `import.meta.env.DEV` (true): the stored preview is
// honoured and the toggle is offered. The production configuration is
// previewToolsProduction.test.tsx. The initializer and the actions take the build flag
// as a parameter, so both answers are checked here directly as well.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PREVIEW_TOOLS } from "./previewTools";
import { buildActions, initialState, STORAGE_KEY } from "./store";

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

function storeFresh(value: unknown) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ fresh: value }));
}

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

describe("the stored empty-state preview, at initialization", () => {
  it("is dropped in a build without the preview tool", () => {
    storeFresh(true);
    expect(initialState(false).fresh).toBe(false);
  });

  it("is honoured in a build with the preview tool", () => {
    storeFresh(true);
    expect(initialState(true).fresh).toBe(true);
  });

  it("is honoured only as a literal true", () => {
    storeFresh("yes");
    expect(initialState(true).fresh).toBe(false);
  });

  it("defaults to this build's flag, which is on in the test build", () => {
    expect(PREVIEW_TOOLS).toBe(true);
    storeFresh(true);
    expect(initialState().fresh).toBe(true);
  });
});

describe("toggleFresh", () => {
  it("does nothing without the preview tool", () => {
    const dispatch = vi.fn();
    buildActions(dispatch, false).toggleFresh();
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("flips the preview with the tool", () => {
    const dispatch = vi.fn();
    buildActions(dispatch, true).toggleFresh();
    expect(dispatch).toHaveBeenCalledTimes(1);
  });
});

describe("a development build", () => {
  it("shows the stored preview and offers the toggle", () => {
    storeFresh(true);
    render(
      <PerfLabProvider>
        <AppShell />
      </PerfLabProvider>,
    );
    expect(screen.getByText("Welcome to Perf Lab")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Exit empty-state preview" })).toBeTruthy();
  });
});
