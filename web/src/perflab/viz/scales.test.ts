import { describe, expect, it } from "vitest";

import {
  areaPath,
  bandScale,
  compact,
  linePath,
  linearScale,
  niceTicks,
  radial,
  type Vec2,
} from "./scales";

// Parse a radial polygon string ("x,y x,y ...") into numeric points.
function parsePolygon(s: string): Vec2[] {
  if (!s) return [];
  return s.split(" ").map((pair) => {
    const [x, y] = pair.split(",").map(Number);
    return [x, y] as Vec2;
  });
}

const allFinite = (pts: Vec2[]) => pts.every(([x, y]) => Number.isFinite(x) && Number.isFinite(y));

describe("linearScale", () => {
  it("maps an ascending domain onto an ascending range", () => {
    const s = linearScale({ domain: [0, 10], range: [0, 100] });
    expect(s(0)).toBeCloseTo(0);
    expect(s(5)).toBeCloseTo(50);
    expect(s(10)).toBeCloseTo(100);
  });

  it("flips for an inverted range (Y axis: [bottom, top])", () => {
    const s = linearScale({ domain: [0, 10], range: [100, 0] });
    expect(s(0)).toBeCloseTo(100);
    expect(s(10)).toBeCloseTo(0);
  });

  it("handles an inverted domain", () => {
    const s = linearScale({ domain: [10, 0], range: [0, 100] });
    expect(s(10)).toBeCloseTo(0);
    expect(s(0)).toBeCloseTo(100);
  });

  it("extrapolates outside the domain when clamp=false (both ends)", () => {
    const s = linearScale({ domain: [0, 10], range: [0, 100] });
    expect(s(-5)).toBeCloseTo(-50);
    expect(s(15)).toBeCloseTo(150);
  });

  it("clamps to the nearest range endpoint when clamp=true (both ends)", () => {
    const s = linearScale({ domain: [0, 10], range: [0, 100], clamp: true });
    expect(s(-5)).toBeCloseTo(0); // below domain -> range start
    expect(s(15)).toBeCloseTo(100); // above domain -> range end
  });

  it("maps every input to the range start for a zero-width domain", () => {
    const s = linearScale({ domain: [5, 5], range: [20, 120] });
    expect(s(999)).toBeCloseTo(20);
    expect(s(-3)).toBeCloseTo(20);
  });

  it("returns the domain start when inverting a zero-width range", () => {
    const s = linearScale({ domain: [7, 42], range: [50, 50] });
    expect(s.invert(123)).toBeCloseTo(7);
  });

  // Round-trip property: invert(scale(x)) ≈ x for nondegenerate scales, both directions.
  const sampleXs = [0, 2.5, 5, 7.5, 10];
  it.each([
    ["ascending range", [0, 100] as Vec2],
    ["descending range", [100, 0] as Vec2],
  ])("round-trips invert(scale(x)) ≈ x (%s)", (_label, range) => {
    const s = linearScale({ domain: [0, 10], range });
    for (const x of sampleXs) {
      expect(s.invert(s(x))).toBeCloseTo(x);
    }
  });
});

describe("linePath", () => {
  it("returns an empty string for no points", () => {
    expect(linePath([])).toBe("");
  });

  it("emits a single move command for one point (no line segment)", () => {
    expect(linePath([[1, 2]])).toBe("M1.00,2.00");
  });

  it("emits M then L commands for multiple points", () => {
    expect(
      linePath([
        [0, 0],
        [10, 20],
      ]),
    ).toBe("M0.00,0.00 L10.00,20.00");
  });

  it("contains no NaN or undefined for finite input", () => {
    const out = linePath([
      [1.234, 5.678],
      [9.1, 2.3],
    ]);
    expect(out).not.toMatch(/NaN|undefined/);
  });
});

describe("areaPath", () => {
  it("returns an empty string for no points", () => {
    expect(areaPath([], 100)).toBe("");
  });

  it("closes the path with Z and drops to the baseline", () => {
    const out = areaPath(
      [
        [0, 10],
        [10, 30],
      ],
      100,
    );
    expect(out.startsWith("M0.00,10.00")).toBe(true);
    expect(out.endsWith("Z")).toBe(true);
    expect(out).toContain("100.00"); // baseline used
  });

  it("produces a degenerate but valid closed path for one point", () => {
    const out = areaPath([[5, 5]], 50);
    expect(out.startsWith("M5.00,5.00")).toBe(true);
    expect(out.endsWith("Z")).toBe(true);
  });
});

describe("compact (already-correct outputs)", () => {
  // Boundary-adjacent around the display thresholds that are NOT affected by unit carry.
  it.each([
    [0, "0"],
    [-0, "0"],
    [999, "999"],
    [-999, "-999"],
    [1000, "1,000"],
    [9999, "9,999"],
    [10_000, "10K"],
    [1_000_000, "1M"],
    [10_000_000, "10M"],
  ])("compact(%i) === %s", (n, expected) => {
    expect(compact(n)).toBe(expected);
  });

  it("renders negatives with the same magnitude, opposite sign", () => {
    expect(compact(-10_000)).toBe("-10K");
    expect(compact(-1_000_000)).toBe("-1M");
  });
});

describe("niceTicks (ascending domains)", () => {
  it("returns a single tick when the bounds are equal", () => {
    expect(niceTicks(5, 5)).toEqual([5]);
  });

  it("produces monotonic, finite, uniformly spaced ticks over a positive range", () => {
    const ticks = niceTicks(0, 10);
    expect(ticks.length).toBeGreaterThan(1);
    expect(ticks.every(Number.isFinite)).toBe(true);
    for (let i = 1; i < ticks.length; i++) {
      expect(ticks[i]).toBeGreaterThan(ticks[i - 1]);
      expect(ticks[i] - ticks[i - 1]).toBeCloseTo(ticks[1] - ticks[0]);
    }
  });

  it("handles a negative-only range", () => {
    const ticks = niceTicks(-100, -10);
    expect(ticks.every((v) => v <= 0)).toBe(true);
    expect(ticks.every(Number.isFinite)).toBe(true);
  });

  it("brackets a range that crosses zero", () => {
    const ticks = niceTicks(-3, 7);
    expect(Math.min(...ticks)).toBeLessThanOrEqual(0);
    expect(Math.max(...ticks)).toBeGreaterThanOrEqual(0);
  });

  it("never emits negative zero", () => {
    for (const [lo, hi] of [
      [-3, 7],
      [-10, 10],
      [-1, 1],
    ]) {
      expect(niceTicks(lo, hi).some((v) => Object.is(v, -0))).toBe(false);
    }
  });
});

describe("bandScale (positive integer count, ascending range)", () => {
  it("exposes the requested count", () => {
    expect(bandScale({ count: 7, range: [0, 700] }).count).toBe(7);
  });

  it("produces finite, uniformly spaced, in-range centers with nonnegative bandWidth", () => {
    const range: Vec2 = [0, 100];
    const b = bandScale({ count: 4, range });
    const centers = Array.from({ length: 4 }, (_, i) => b.center(i));
    expect(centers.every(Number.isFinite)).toBe(true);
    // uniform spacing
    for (let i = 1; i < centers.length; i++) {
      expect(centers[i] - centers[i - 1]).toBeCloseTo(b.step);
    }
    // nonnegative bandWidth
    expect(b.bandWidth).toBeGreaterThanOrEqual(0);
    // bands within the range extent
    for (let i = 0; i < 4; i++) {
      expect(b.start(i)).toBeGreaterThanOrEqual(range[0] - 1e-9);
      expect(b.start(i) + b.bandWidth).toBeLessThanOrEqual(range[1] + 1e-9);
    }
  });
});

describe("radial (count > 0)", () => {
  it("fills missing values with zero for short arrays", () => {
    const r = radial({ cx: 50, cy: 50, r: 40, count: 3 });
    const short = parsePolygon(r.valuePolygon([1])); // 2 missing -> 0
    expect(short).toHaveLength(3);
    expect(allFinite(short)).toBe(true);
  });

  it("ignores extra values beyond count (output length === count)", () => {
    const r = radial({ cx: 50, cy: 50, r: 40, count: 3 });
    const long = parsePolygon(r.valuePolygon([0.2, 0.4, 0.6, 0.8, 1]));
    expect(long).toHaveLength(3);
  });

  it("keeps all coordinates finite for a single value and all-zero values", () => {
    const r = radial({ cx: 50, cy: 50, r: 40, count: 5 });
    expect(allFinite(parsePolygon(r.valuePolygon([0.5])))).toBe(true);
    expect(allFinite(parsePolygon(r.valuePolygon([0, 0, 0, 0, 0])))).toBe(true);
  });

  it("returns finite spoke and grid geometry", () => {
    const r = radial({ cx: 50, cy: 50, r: 40, count: 6 });
    expect(r.spoke(0).every(Number.isFinite)).toBe(true);
    expect(allFinite(parsePolygon(r.gridPolygon(0.5)))).toBe(true);
  });
});
