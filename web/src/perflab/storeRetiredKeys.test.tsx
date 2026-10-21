// @vitest-environment jsdom
//
// src/perflab/storeRetiredKeys.test.tsx
//
// B2 retired two persisted values: `fieldTest` (a cached /compute-metrics result — miscalculated:
// 163 used as kg, sex fixed at male) and `settings.sex` (a device-wide "Female" default the VO₂
// estimate must never read). Neither may be read back, and both must leave localStorage.
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PerfLabProvider } from "./PerfLabProvider";
import { initialState, STORAGE_KEY } from "./store";

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

const OLD = {
  ftDone: true,
  fieldTest: { vo2_max: 31.6, vo2_category: "Below Average", fatigue_percent: -33 },
  settings: { sex: "Female", units: "Imperial (mi)" },
};

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
  localStorage.setItem(STORAGE_KEY, JSON.stringify(OLD));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("reads neither retired value back into state", () => {
  const state = initialState(false) as unknown as Record<string, unknown> & {
    settings: Record<string, unknown>;
  };
  expect(state).not.toHaveProperty("fieldTest");
  expect(state.settings).not.toHaveProperty("sex");
  expect(state.settings.units).toBe("Imperial (mi)"); // the rest is kept
  expect(state.ftDone).toBe(true); // ftDone stays
});

it("drops both from localStorage once the app has loaded", async () => {
  render(<PerfLabProvider>{null}</PerfLabProvider>);
  await waitFor(() => {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    expect(stored).not.toHaveProperty("fieldTest");
    expect(stored.settings).not.toHaveProperty("sex");
    expect(stored.settings.units).toBe("Imperial (mi)");
    expect(stored.ftDone).toBe(true);
  });
});
