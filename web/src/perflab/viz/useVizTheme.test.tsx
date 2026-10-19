// @vitest-environment jsdom
//
// useVizTheme resolves two independent inputs into concrete colors:
//   * mode   — next-themes' `resolvedTheme` ("light" → light, anything else → dark)
//   * accent — the `--ac` custom property on <html>, re-read via a MutationObserver
// Mode is driven through the real next-themes ThemeProvider (configured as in
// main.tsx), and accent through the real <html> style attribute, so the tests
// observe the hook's output rather than its internals.
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { ThemeProvider, useTheme } from "next-themes";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { chrome } from "./chrome";
import { COLORS, PALETTE } from "./tokens";
import { useVizTheme } from "./useVizTheme";

// next-themes' provider subscribes to prefers-color-scheme on mount; jsdom has no
// matchMedia. enableSystem is false (as in main.tsx), so the result is never used.
window.matchMedia ??= ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: () => {},
  removeListener: () => {},
  addEventListener: () => {},
  removeEventListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

function themed(defaultTheme: "light" | "dark") {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <ThemeProvider attribute="class" defaultTheme={defaultTheme} enableSystem={false}>
        {children}
      </ThemeProvider>
    );
  };
}

beforeEach(() => {
  // next-themes persists the chosen theme; reset it so tests stay independent.
  // Node's own global localStorage can throw without --localstorage-file, and
  // next-themes swallows storage errors itself, so this reset is best-effort too.
  try {
    window.localStorage.clear();
  } catch {
    /* storage unavailable: nothing persisted to clear */
  }
  document.documentElement.removeAttribute("style");
  document.documentElement.removeAttribute("class");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("useVizTheme — default resolution", () => {
  it("resolves to dark with the lime accent when there is no ThemeProvider and no --ac", () => {
    const { result } = renderHook(() => useVizTheme());
    expect(result.current.mode).toBe("dark");
    expect(result.current.colors).toBe(PALETTE.dark);
    expect(result.current.chrome).toEqual(chrome("dark"));
    expect(result.current.accent).toBe(COLORS.lime);
  });

  it("resolves to light under a provider whose theme is light", () => {
    const { result } = renderHook(() => useVizTheme(), { wrapper: themed("light") });
    expect(result.current.mode).toBe("light");
    expect(result.current.colors).toBe(PALETTE.light);
    expect(result.current.chrome).toEqual(chrome("light"));
  });

  it("reads an --ac already present on <html> at mount", () => {
    document.documentElement.style.setProperty("--ac", "#123456");
    const { result } = renderHook(() => useVizTheme());
    expect(result.current.accent).toBe("#123456");
  });
});

describe("useVizTheme — updates", () => {
  it("follows the theme mode when it changes", () => {
    const { result } = renderHook(() => ({ viz: useVizTheme(), theme: useTheme() }), {
      wrapper: themed("dark"),
    });
    expect(result.current.viz.mode).toBe("dark");

    act(() => result.current.theme.setTheme("light"));
    expect(result.current.viz.mode).toBe("light");
    expect(result.current.viz.colors).toBe(PALETTE.light);
    expect(result.current.viz.chrome).toEqual(chrome("light"));

    act(() => result.current.theme.setTheme("dark"));
    expect(result.current.viz.mode).toBe("dark");
    expect(result.current.viz.colors).toBe(PALETTE.dark);
  });

  it("recolors the accent when --ac is changed on <html> after mount", async () => {
    const { result } = renderHook(() => useVizTheme());
    expect(result.current.accent).toBe(COLORS.lime);

    act(() => document.documentElement.style.setProperty("--ac", "#ff00aa"));
    await waitFor(() => expect(result.current.accent).toBe("#ff00aa"));

    act(() => document.documentElement.style.removeProperty("--ac"));
    await waitFor(() => expect(result.current.accent).toBe(COLORS.lime));
  });
});

describe("useVizTheme — cleanup", () => {
  it("disconnects its <html> observer on unmount, and not before", () => {
    const disconnect = vi.spyOn(MutationObserver.prototype, "disconnect");
    const { unmount } = renderHook(() => useVizTheme());
    expect(disconnect).not.toHaveBeenCalled();
    unmount();
    expect(disconnect).toHaveBeenCalledTimes(1);
  });
});
