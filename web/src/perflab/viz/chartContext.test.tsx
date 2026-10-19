// @vitest-environment jsdom
//
// chartContext: the context defaults to null, and useChart() turns "no enclosing
// <Chart>" into a loud error instead of letting a mark read undefined geometry.
import { cleanup, renderHook } from "@testing-library/react";
import { useContext, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Chart } from "./Chart";
import { ChartContext, useChart, type ChartCtx } from "./chartContext";
import { chrome } from "./chrome";
import { PALETTE } from "./tokens";

// Chart sizes itself with ResizeObserver, which jsdom does not provide.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const CTX: ChartCtx = {
  width: 300,
  height: 120,
  plot: { x: 10, y: 5, w: 280, h: 100 },
  colors: PALETTE.dark,
  chrome: chrome("dark"),
  mode: "dark",
  accent: "#c6f135",
};

describe("chartContext with no provider", () => {
  it("the raw context value is null", () => {
    const { result } = renderHook(() => useContext(ChartContext));
    expect(result.current).toBeNull();
  });

  it("useChart throws a descriptive error", () => {
    // React reports the render error to console.error before rethrowing.
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useChart())).toThrow("viz marks must be rendered inside a <Chart>");
  });
});

describe("chartContext with a provider", () => {
  it("useChart returns exactly the provided value", () => {
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ChartContext.Provider value={CTX}>{children}</ChartContext.Provider>
    );
    const { result } = renderHook(() => useChart(), { wrapper });
    expect(result.current).toBe(CTX);
  });

  it("a real <Chart> provides its plot rect, scales and theme to marks", () => {
    const wrapper = ({ children }: { children: ReactNode }) => (
      <Chart
        width={200}
        height={100}
        padding={{ left: 20, bottom: 20 }}
        ariaLabel="probe"
        xDomain={[0, 4]}
        yDomain={[0, 100]}
      >
        {children}
      </Chart>
    );
    const { result } = renderHook(() => useChart(), { wrapper });
    const ctx = result.current;
    expect(ctx.width).toBe(200);
    expect(ctx.height).toBe(100);
    expect(ctx.plot).toEqual({ x: 20, y: 0, w: 180, h: 80 });
    expect(ctx.mode).toBe("dark");
    expect(ctx.colors).toBe(PALETTE.dark);
    expect(ctx.xScale?.(0)).toBe(20);
    expect(ctx.xScale?.(4)).toBe(200);
    // y is inverted: the domain floor sits at the plot bottom.
    expect(ctx.yScale?.(0)).toBe(80);
    expect(ctx.yScale?.(100)).toBe(0);
    // Interactive by default with an x scale → series registration is offered.
    expect(typeof ctx.register).toBe("function");
  });

  it("a <Chart> without an x scale offers no series registration", () => {
    const wrapper = ({ children }: { children: ReactNode }) => (
      <Chart width={100} height={50} ariaLabel="probe" yDomain={[0, 1]}>
        {children}
      </Chart>
    );
    const ctx = renderHook(() => useChart(), { wrapper }).result.current;
    expect(ctx.xScale).toBeUndefined();
    expect(ctx.register).toBeUndefined();
    expect(ctx.unregister).toBeUndefined();
  });
});
