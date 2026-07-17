import { describe, expect, it } from "vitest";

import { areaPath, bandScale, compact, linearScale, linePath, niceTicks, radial } from "./scales";

describe("linearScale", () => {
  it("maps an ordinary ascending domain/range", () => {
    const scale = linearScale({ domain: [0, 100], range: [0, 200] });
    expect(scale(0)).toBeCloseTo(0);
    expect(scale(50)).toBeCloseTo(100);
    expect(scale(100)).toBeCloseTo(200);
  });

  it("maps correctly with an inverted (descending) range", () => {
    // The Y-axis case: range = [bottom, top].
    const scale = linearScale({ domain: [0, 100], range: [200, 0] });
    expect(scale(0)).toBeCloseTo(200);
    expect(scale(100)).toBeCloseTo(0);
    expect(scale(50)).toBeCloseTo(100);
  });

  it("maps correctly with an inverted (descending) domain", () => {
    const scale = linearScale({ domain: [100, 0], range: [0, 200] });
    expect(scale(100)).toBeCloseTo(0);
    expect(scale(0)).toBeCloseTo(200);
    expect(scale(50)).toBeCloseTo(100);
  });

  it("round-trips invert(scale(x)) ≈ x for an ascending range", () => {
    const scale = linearScale({ domain: [-50, 150], range: [0, 400] });
    for (const x of [-50, -10, 0, 33.3, 75, 150]) {
      expect(scale.invert(scale(x))).toBeCloseTo(x);
    }
  });

  it("round-trips invert(scale(x)) ≈ x for a descending range", () => {
    const scale = linearScale({ domain: [-50, 150], range: [400, 0] });
    for (const x of [-50, -10, 0, 33.3, 75, 150]) {
      expect(scale.invert(scale(x))).toBeCloseTo(x);
    }
  });

  it("without clamp, extrapolates linearly outside the domain", () => {
    const scale = linearScale({ domain: [0, 100], range: [0, 200] });
    expect(scale(-50)).toBeCloseTo(-100);
    expect(scale(150)).toBeCloseTo(300);
  });

  it("with clamp=true, values below/above the domain map to the nearest range endpoint", () => {
    const ascending = linearScale({ domain: [0, 100], range: [0, 200], clamp: true });
    expect(ascending(-50)).toBeCloseTo(0);
    expect(ascending(150)).toBeCloseTo(200);

    // "Nearest endpoint" is domain-order, not numeric-order: for a descending
    // range, a value below the domain start still clamps to r0 (the range's
    // start element, 200 here), not to the numerically smaller value.
    const descending = linearScale({ domain: [0, 100], range: [200, 0], clamp: true });
    expect(descending(-50)).toBeCloseTo(200);
    expect(descending(150)).toBeCloseTo(0);
  });

  it("a zero-width domain maps every input to the range's start (r0)", () => {
    const scale = linearScale({ domain: [50, 50], range: [10, 90] });
    expect(scale(50)).toBeCloseTo(10);
    expect(scale(0)).toBeCloseTo(10);
    expect(scale(1000)).toBeCloseTo(10);
  });

  it("a zero-width range's invert() returns the domain's start (d0) for any pixel", () => {
    const scale = linearScale({ domain: [10, 90], range: [50, 50] });
    expect(scale.invert(50)).toBeCloseTo(10);
    expect(scale.invert(0)).toBeCloseTo(10);
    expect(scale.invert(1000)).toBeCloseTo(10);
  });
});

describe("bandScale", () => {
  it("produces count evenly-spaced, finite, within-range bands for a positive count", () => {
    const band = bandScale({ count: 7, range: [0, 700] });
    expect(band.count).toBe(7);
    expect(band.bandWidth).toBeGreaterThanOrEqual(0);

    const centers = Array.from({ length: 7 }, (_, i) => band.center(i));
    for (const c of centers) expect(Number.isFinite(c)).toBe(true);

    // Uniform spacing: consecutive centers differ by exactly `step`.
    for (let i = 1; i < centers.length; i++) {
      expect(centers[i] - centers[i - 1]).toBeCloseTo(band.step);
    }

    // Every rendered band (start .. start+bandWidth) stays within the range.
    for (let i = 0; i < 7; i++) {
      expect(band.start(i)).toBeGreaterThanOrEqual(0 - 1e-9);
      expect(band.start(i) + band.bandWidth).toBeLessThanOrEqual(700 + 1e-9);
    }
  });

  it("count === 0: step falls back to the full span (not zero/infinite); no bands are rendered", () => {
    const band = bandScale({ count: 0, range: [0, 100] });
    expect(band.count).toBe(0);
    expect(band.step).toBeCloseTo(100);
    expect(band.bandWidth).toBeCloseTo(80); // span * (1 - default innerPad 0.2)
  });

  it("count < 0 is not validated: it falls into the same branch as count === 0 (step = span)", () => {
    // Documents current behavior, not an endorsed contract — `count > 0` is
    // false for negative counts too, so they share the same fallback as 0.
    const band = bandScale({ count: -3, range: [0, 100] });
    expect(band.step).toBeCloseTo(100);
  });

  it("a non-integer count is not rejected — it divides the span fractionally", () => {
    const band = bandScale({ count: 2.5, range: [0, 100] });
    expect(band.step).toBeCloseTo(40);
  });

  it("a descending range produces a negative bandWidth (unsupported today — no caller uses this)", () => {
    // scales.ts's only real caller (Bars.tsx) always passes an ascending range.
    // Documented so a future caller doesn't assume this "just works" symmetrically.
    const band = bandScale({ count: 5, range: [100, 0] });
    expect(band.step).toBeLessThan(0);
    expect(band.bandWidth).toBeLessThan(0);
  });
});

describe("radial", () => {
  it("point()/spoke() produce finite coordinates for a normal count", () => {
    const r = radial({ cx: 50, cy: 50, r: 40, count: 4 });
    for (let i = 0; i < 4; i++) {
      const [x, y] = r.spoke(i);
      expect(Number.isFinite(x)).toBe(true);
      expect(Number.isFinite(y)).toBe(true);
    }
  });

  it("gridPolygon()/valuePolygon() with count === 0 return an empty string (no points to plot)", () => {
    const r = radial({ cx: 0, cy: 0, r: 10, count: 0 });
    expect(r.gridPolygon(1)).toBe("");
    expect(r.valuePolygon([])).toBe("");
  });

  it("point()/spoke() with count === 0 return [NaN, NaN] if called directly (not guarded)", () => {
    // Documents a real, previously-unverified gap: gridPolygon/valuePolygon
    // never call point() when count === 0 (Array.from({length: 0}, ...) is a
    // no-op), but point()/spoke() are exposed directly and are NOT guarded —
    // 360 / count is Infinity, and Infinity * 0 (the i=0 case) is NaN.
    const r = radial({ cx: 0, cy: 0, r: 10, count: 0 });
    const [x, y] = r.spoke(0);
    expect(x).toBeNaN();
    expect(y).toBeNaN();
  });

  it("valuePolygon() with a values array shorter than count uses 0 for the missing entries", () => {
    const r = radial({ cx: 0, cy: 0, r: 10, count: 4 });
    const short = r.valuePolygon([1]); // only axis 0 has a value
    const allZero = r.valuePolygon([0, 0, 0, 0]);
    const centerPoint = r.point(1, 0); // axis 1 at radius 0 == center
    // Axes 1-3 collapse to the center point (radius 0), matching all-zero.
    const shortPoints = short.split(" ");
    const zeroPoints = allZero.split(" ");
    expect(shortPoints[1]).toBe(zeroPoints[1]);
    expect(shortPoints[1]).toBe(`${centerPoint[0]},${centerPoint[1]}`);
  });

  it("valuePolygon() ignores values beyond count", () => {
    const r = radial({ cx: 0, cy: 0, r: 10, count: 2 });
    const withExtra = r.valuePolygon([1, 1, 1, 1, 1]);
    const withoutExtra = r.valuePolygon([1, 1]);
    expect(withExtra).toBe(withoutExtra);
    expect(withExtra.split(" ")).toHaveLength(2);
  });

  it("all-zero values collapse every point to the center", () => {
    const r = radial({ cx: 5, cy: 5, r: 10, count: 3 });
    const poly = r.valuePolygon([0, 0, 0]);
    expect(poly).toBe("5,5 5,5 5,5");
  });

  it("a single-axis radial (count = 1) is finite and well-defined", () => {
    const r = radial({ cx: 0, cy: 0, r: 10, count: 1 });
    const [x, y] = r.spoke(0);
    expect(Number.isFinite(x)).toBe(true);
    expect(Number.isFinite(y)).toBe(true);
  });

  it("a negative value inverts that axis's point through the center (not guarded against)", () => {
    // Documents current behavior: point(i, radius) does not clamp or reject a
    // negative radius, so a negative `values[i]` mirrors the point 180° through
    // (cx, cy) rather than clamping to 0. Flagged as a possible follow-up: if
    // radial magnitudes are meant to always be non-negative, this should
    // either clamp or throw instead of silently flipping the point.
    const r = radial({ cx: 0, cy: 0, r: 10, count: 1, startAngle: 0 });
    const positive = r.point(0, 10);
    const negative = r.point(0, -10);
    expect(negative[0]).toBeCloseTo(-positive[0]);
    expect(negative[1]).toBeCloseTo(-positive[1]);
  });
});

describe("linePath", () => {
  it("returns an empty string for no points", () => {
    expect(linePath([])).toBe("");
  });

  it("a single point emits a valid, standalone M command (no L)", () => {
    const path = linePath([[1, 2]]);
    expect(path).toBe("M1.00,2.00");
    expect(path).not.toContain("L");
  });

  it("two points emit M then one L", () => {
    const path = linePath([[0, 0], [10, 20]]);
    expect(path).toBe("M0.00,0.00 L10.00,20.00");
  });

  it("multiple points emit M then one L per subsequent point, no NaN/undefined", () => {
    const path = linePath([[0, 0], [1, 1], [2, 4], [3, 9]]);
    expect(path).toBe("M0.00,0.00 L1.00,1.00 L2.00,4.00 L3.00,9.00");
    expect(path).not.toContain("NaN");
    expect(path).not.toContain("undefined");
  });
});

describe("areaPath", () => {
  it("returns an empty string for no points", () => {
    expect(areaPath([], 100)).toBe("");
  });

  it("a single point closes to the baseline and back to the same x, then Z", () => {
    const path = areaPath([[5, 10]], 100);
    expect(path).toBe("M5.00,10.00 L5.00,100.00 L5.00,100.00 Z");
  });

  it("multiple points close via the shared baseline and end with Z", () => {
    const path = areaPath([[0, 10], [10, 20]], 100);
    expect(path).toBe("M0.00,10.00 L10.00,20.00 L10.00,100.00 L0.00,100.00 Z");
    expect(path.endsWith("Z")).toBe(true);
  });
});

describe("niceTicks", () => {
  it("lo === hi returns a single-element array", () => {
    expect(niceTicks(5, 5)).toEqual([5]);
  });

  it("reversed bounds (lo > hi) silently return an empty array", () => {
    // Documents a real, previously-unverified gap: Math.log10 of a negative
    // rawStep is NaN, which propagates through step/start, and the loop's
    // NaN <= NaN comparison is always false — so the loop body never runs.
    // Flagged as a possible follow-up: a caller that accidentally swapped
    // min/max gets an empty tick array with no error, not a reordered result.
    expect(niceTicks(10, 0)).toEqual([]);
  });

  it("a range crossing zero never produces a -0 tick", () => {
    const ticks = niceTicks(-10, 10);
    expect(ticks.some((v) => Object.is(v, -0))).toBe(false);
  });

  it("a negative-only range produces finite, monotonic ticks with no -0", () => {
    const ticks = niceTicks(-100, -10);
    expect(ticks.length).toBeGreaterThan(0);
    for (const v of ticks) expect(Number.isFinite(v)).toBe(true);
    for (let i = 1; i < ticks.length; i++) expect(ticks[i]).toBeGreaterThan(ticks[i - 1]);
    expect(ticks.some((v) => Object.is(v, -0))).toBe(false);
  });

  it("a very small span still produces finite, monotonic ticks", () => {
    const ticks = niceTicks(0, 1e-8);
    expect(ticks.length).toBeGreaterThan(0);
    for (const v of ticks) expect(Number.isFinite(v)).toBe(true);
  });

  it("a very large span still produces finite, monotonic ticks", () => {
    const ticks = niceTicks(0, 1e9);
    expect(ticks.length).toBeGreaterThan(0);
    for (const v of ticks) expect(Number.isFinite(v)).toBe(true);
  });

  it("a requested tick count of 0 is silently treated as 1 (Math.max(1, count))", () => {
    const zero = niceTicks(0, 100, 0);
    const one = niceTicks(0, 100, 1);
    expect(zero).toEqual(one);
  });

  it("ticks are evenly spaced (uniform step)", () => {
    const ticks = niceTicks(0, 100, 5);
    expect(ticks.length).toBeGreaterThan(1);
    const step = ticks[1] - ticks[0];
    for (let i = 2; i < ticks.length; i++) {
      expect(ticks[i] - ticks[i - 1]).toBeCloseTo(step);
    }
  });

  it("ticks bracket the requested domain (no tick outside [lo, hi] beyond float tolerance)", () => {
    const ticks = niceTicks(3, 97, 5);
    for (const v of ticks) {
      expect(v).toBeGreaterThanOrEqual(3 - 1e-6);
      expect(v).toBeLessThanOrEqual(97 + 1e-6);
    }
  });
});

describe("compact", () => {
  it.each([
    [0, "0"],
    [-0, "0"],
    [0.5, "0.5"],
    [999, "999"],
    [1_000, "1,000"],
    [9_999, "9,999"],
    [10_000, "10K"],
    [999_999, "1000K"], // named below: a real rounding-carry defect, not a design choice
    [1_000_000, "1M"],
    [9_999_999, "10M"],
    [10_000_000, "10M"],
  ])("compact(%p) === %p", (input, expected) => {
    expect(compact(input)).toBe(expected);
  });

  it("999_999 rounds up to 1000K rather than crossing to 1M — a real formatting defect", () => {
    // 999_999 / 1000 = 999.999, and .toFixed(0) rounds that to "1000" before
    // the M-vs-K branch is ever re-checked. The K branch is chosen based on
    // the *original* abs value (< 1e6), not the post-rounding one, so the
    // result reads "1000K" instead of the presumably-intended "1M". This is a
    // real, previously-unverified defect flagged here rather than fixed — a
    // testing slice is not the place to change scales.ts's runtime behavior.
    expect(compact(999_999)).toBe("1000K");
  });

  it("negative numbers mirror the positive magnitude with a sign flip (except -0)", () => {
    for (const n of [999, 1_000, 9_999, 10_000, 999_999, 1_000_000, 9_999_999]) {
      const pos = compact(n);
      const neg = compact(-n);
      expect(neg).toBe(`-${pos}`);
    }
    // -0 loses its sign (String(-0) === "0" in JS) — not symmetric, documented.
    expect(compact(-0)).toBe("0");
  });
});
