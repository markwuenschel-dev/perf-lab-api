// @vitest-environment jsdom
//
// Chart is the frame every mark draws in. For a small fixed dataset this checks
// the rendered SVG: the accessible root, the viewBox fallback, the y axis ticks +
// gridlines, the x labels, and the series path at its exact pixel coordinates.
//
// Fixture geometry (jsdom has no layout, so the viewBox falls back to the props):
//   width 200 × height 100, padding left 20 / bottom 20 → plot x 20, y 0, w 180, h 80
//   xDomain [0, 4] → x range [20, 200] (45 px per step)
//   yDomain [0, 100] → y range [80, 0]  (inverted: 0 sits on the baseline)
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Axis } from "./Axis";
import { Chart } from "./Chart";
import { Line } from "./Line";
import type { Vec2 } from "./scales";

// Chart sizes itself with ResizeObserver, which jsdom does not provide.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

afterEach(cleanup);

const DATA: Vec2[] = [
  [0, 0],
  [1, 50],
  [2, 25],
  [3, 100],
  [4, 75],
];
const X_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri"];

function renderFixture() {
  return render(
    <Chart
      width={200}
      height={100}
      padding={{ left: 20, bottom: 20 }}
      ariaLabel="Weekly load"
      xDomain={[0, 4]}
      yDomain={[0, 100]}
    >
      <Axis y x xLabels={X_LABELS} />
      <Line data={DATA} label="Load" color="#ff0000" />
    </Chart>,
  );
}

describe("Chart", () => {
  it("renders an accessible svg root sized from the props", () => {
    const { container } = renderFixture();
    const svg = container.querySelector("svg")!;
    expect(svg).not.toBeNull();
    expect(svg.getAttribute("role")).toBe("img");
    expect(svg.getAttribute("aria-label")).toBe("Weekly load");
    expect(svg.getAttribute("viewBox")).toBe("0 0 200 100");
  });

  it("draws the series at its scaled pixel positions", () => {
    const { container } = renderFixture();
    const paths = container.querySelectorAll("path");
    expect(paths).toHaveLength(1);
    expect(paths[0].getAttribute("d")).toBe(
      "M20.00,80.00 L65.00,40.00 L110.00,60.00 L155.00,0.00 L200.00,20.00",
    );
    expect(paths[0].getAttribute("stroke")).toBe("#ff0000");
  });

  it("draws y ticks with a gridline each, inverted onto the plot", () => {
    const { container } = renderFixture();
    const yLabels = Array.from(container.querySelectorAll('text[text-anchor="end"]'));
    expect(yLabels.map((t) => t.textContent)).toEqual(["0", "20", "40", "60", "80", "100"]);
    expect(yLabels.map((t) => t.getAttribute("y"))).toEqual(["80", "64", "48", "32", "16", "0"]);
    // Labels sit just left of the plot.
    expect(yLabels.every((t) => t.getAttribute("x") === "14")).toBe(true);

    // One full-width gridline per tick, plus the x baseline at the plot bottom.
    const lines = Array.from(container.querySelectorAll("line"));
    const fullWidth = lines.filter((l) => l.getAttribute("x1") === "20" && l.getAttribute("x2") === "200");
    expect(fullWidth.map((l) => l.getAttribute("y1"))).toEqual(["80", "64", "48", "32", "16", "0", "80"]);
  });

  it("places one x label under each data index", () => {
    const { container } = renderFixture();
    const xLabels = Array.from(container.querySelectorAll('text[text-anchor="middle"]'));
    expect(xLabels.map((t) => t.textContent)).toEqual(X_LABELS);
    expect(xLabels.map((t) => t.getAttribute("x"))).toEqual(["20", "65", "110", "155", "200"]);
    expect(xLabels.every((t) => t.getAttribute("y") === "94")).toBe(true);
  });

  it("shows no crosshair or tooltip before any pointer interaction", () => {
    const { container } = renderFixture();
    expect(container.querySelectorAll("circle")).toHaveLength(0);
    // The only top-level child of the wrapper is the svg (no tooltip box).
    expect(container.firstElementChild!.children).toHaveLength(1);
  });
});
