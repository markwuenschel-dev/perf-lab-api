// Pure scale + geometry helpers for the viz layer.
//
// Every screen used to re-derive these inline (`ox/oy` in Overview, `hx/hy` in
// History, `cx/cy/ry` in Simulator, `pxc/ppy` in Planning, radar trig in Twin).
// They are all the same two maps — a linear value→pixel scale and, for the radar,
// polar placement. Centralised here so there is one implementation to reason about
// and unit-test. No React, no DOM — safe to import anywhere.

export type Vec2 = readonly [number, number];

export interface LinearScaleOpts {
  /** Data domain [lo, hi]. */
  domain: readonly [number, number];
  /** Pixel range [start, end]. For a Y axis pass [bottom, top] to flip. */
  range: readonly [number, number];
  /** Clamp outputs to the range ends (default false). */
  clamp?: boolean;
}

export interface LinearScale {
  (value: number): number;
  domain: readonly [number, number];
  range: readonly [number, number];
  /** Inverse map: pixel → data value (used by crosshair hit-testing). */
  invert(px: number): number;
}

/** A linear value→pixel scale. `range` may be inverted (Y axes) or degenerate. */
export function linearScale({ domain, range, clamp = false }: LinearScaleOpts): LinearScale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const dspan = d1 - d0;
  const rspan = r1 - r0;
  const scale = ((value: number) => {
    const t = dspan === 0 ? 0 : (value - d0) / dspan;
    const tc = clamp ? Math.min(1, Math.max(0, t)) : t;
    return r0 + tc * rspan;
  }) as LinearScale;
  scale.domain = domain;
  scale.range = range;
  scale.invert = (px: number) => (rspan === 0 ? d0 : d0 + ((px - r0) / rspan) * dspan);
  return scale;
}

export interface BandScaleOpts {
  /** Number of bands (e.g. 7 for Mon–Sun). */
  count: number;
  /** Pixel range [start, end] the bands are laid across. */
  range: readonly [number, number];
  /** Fraction of each step left as gap between bands, 0–1 (default 0.2). */
  innerPad?: number;
}

export interface BandScale {
  /** Center x of band i. */
  center(i: number): number;
  /** Left edge x of band i. */
  start(i: number): number;
  /** Rendered width of a band (after inner padding). */
  bandWidth: number;
  /** Full step (band + gap). */
  step: number;
  count: number;
}

/** Evenly spaced bands for categorical columns/bars.
 *
 * `count === 0` is valid emptiness (an empty dataset): zero step and bandwidth, positions
 * collapse to the range start — never a nonzero bandwidth for zero categories. Negative or
 * non-integer counts are a programmer error and are rejected, rather than silently
 * normalized (which would hide an upstream bug behind a plausible but wrong chart).
 * Descending ranges are supported: `step` carries direction so positions descend, while
 * `bandWidth` stays a nonnegative magnitude. */
export function bandScale({ count, range, innerPad = 0.2 }: BandScaleOpts): BandScale {
  if (!Number.isInteger(count) || count < 0) {
    throw new RangeError(`bandScale: count must be a non-negative integer, got ${count}`);
  }
  const [r0, r1] = range;
  if (count === 0) {
    return { step: 0, bandWidth: 0, count: 0, center: () => r0, start: () => r0 };
  }
  const span = r1 - r0;
  const step = span / count; // carries direction (negative for a descending range)
  const bandWidth = Math.abs(step) * (1 - innerPad); // a width is a magnitude — nonnegative
  return {
    step,
    bandWidth,
    count,
    center: (i: number) => r0 + i * step + step / 2,
    start: (i: number) => r0 + i * step + step / 2 - bandWidth / 2,
  };
}

export interface RadialOpts {
  cx: number;
  cy: number;
  /** Outer radius (value === max sits here). */
  r: number;
  /** Number of axes/spokes. */
  count: number;
  /** Angle of the first spoke, degrees, 0 = up (default -90 → top). */
  startAngle?: number;
}

export interface Radial {
  /** Point for axis i at the given radius. */
  point(i: number, radius: number): Vec2;
  /** Spoke endpoint (axis i at outer radius). */
  spoke(i: number): Vec2;
  /** SVG points string for a grid ring at fraction `frac` of the outer radius. */
  gridPolygon(frac: number): string;
  /** SVG points string for a value polygon; `values` are 0–1 fractions of r. */
  valuePolygon(values: readonly number[]): string;
}

/** Polar placement for radar/spider charts. Replaces the Twin radar trig.
 *
 * `valuePolygon` values are normalized fractions clamped to [0, 1]: a negative fraction
 * would otherwise reflect a point through the center and a >1 fraction would escape the
 * radar boundary — both geometric artifacts with no product meaning. With `count === 0`
 * (an empty dataset) every helper is total: polygons are empty and `point`/`spoke` return
 * the center rather than NaN coordinates that would corrupt SVG geometry downstream. */
export function radial({ cx, cy, r, count, startAngle = -90 }: RadialOpts): Radial {
  const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
  const angle = (i: number) => ((startAngle + (360 / count) * i) * Math.PI) / 180;
  const point = (i: number, radius: number): Vec2 => {
    if (count < 1) return [cx, cy]; // no angular structure -> center
    const a = angle(i);
    return [cx + radius * Math.cos(a), cy + radius * Math.sin(a)];
  };
  const poly = (radiusAt: (i: number) => number) =>
    Array.from({ length: count }, (_, i) => point(i, radiusAt(i)).join(",")).join(" ");
  return {
    point,
    spoke: (i) => point(i, r),
    gridPolygon: (frac) => poly(() => r * frac),
    valuePolygon: (values) => poly((i) => r * clamp01(values[i] ?? 0)),
  };
}

// ── path builders ──────────────────────────────────────────────────────────

/** `M x0 y0 L x1 y1 …` polyline path from points. */
export function linePath(points: readonly Vec2[]): string {
  if (!points.length) return "";
  return points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
}

/** Closed area path from points down to `baselineY` and back. */
export function areaPath(points: readonly Vec2[], baselineY: number): string {
  if (!points.length) return "";
  const [x0] = points[0];
  const [xn] = points[points.length - 1];
  return `${linePath(points)} L${xn.toFixed(2)},${baselineY.toFixed(2)} L${x0.toFixed(2)},${baselineY.toFixed(2)} Z`;
}

// ── ticks ────────────────────────────────────────────────────────────────────

/** "Nice" rounded tick values spanning [lo, hi] (~count ticks).
 *
 * Reversed bounds (`lo > hi`) are supported: ticks are computed over the normalized
 * [min, max] domain and returned in the caller's original direction, so
 * `niceTicks(b, a) === reverse(niceTicks(a, b))`. Reversed domains are valid in
 * visualization (screen-coordinate and ranking axes), so this must not lose them. */
export function niceTicks(lo: number, hi: number, count = 5): number[] {
  if (lo === hi) return [lo];
  const reversed = lo > hi;
  const a = Math.min(lo, hi);
  const b = Math.max(lo, hi);
  const span = b - a;
  const rawStep = span / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag;
  const start = Math.ceil(a / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= b + step * 1e-9; v += step) {
    // guard float drift so 0 prints as 0, not -0 / 1e-16
    ticks.push(Math.abs(v) < step * 1e-9 ? 0 : +v.toFixed(10));
  }
  return reversed ? ticks.reverse() : ticks;
}

/** Compact number formatter for axis ticks / stat values (1,284 / 13K / 4.2M).
 *
 * Unit representation is normalized after rounding: a value that rounds to 1000K is
 * promoted to "1M" rather than rendered as an out-of-range "1000K" (e.g. 999_500 → "1M").
 * The carry therefore happens at the rounding boundary, not only at the raw 1_000_000.
 */
export function compact(n: number): string {
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e4) {
    const k = Math.round(abs / 1e3);
    if (k < 1000) return sign + k + "K";
    const m = abs / 1e6;
    return sign + (abs >= 1e7 ? m.toFixed(0) : m.toFixed(1).replace(/\.0$/, "")) + "M";
  }
  if (abs >= 1e3) return n.toLocaleString("en-US");
  return String(n);
}
