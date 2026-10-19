// @vitest-environment jsdom
//
// Gauge positions a marker knob at `pct` (0–1) along a track. Out-of-range input
// must pin to the track ends, never push the knob outside the bar.
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Gauge, type GaugeProps } from "./Gauge";

afterEach(cleanup);

function renderGauge(props: GaugeProps) {
  const { container } = render(<Gauge {...props} />);
  const root = container.firstElementChild as HTMLDivElement;
  const children = Array.from(root.children) as HTMLDivElement[];
  return { root, children };
}

/** The knob is always the last child (the band, when present, precedes it). */
function knobLeft(props: GaugeProps): string {
  const { children } = renderGauge(props);
  return children[children.length - 1].style.left;
}

describe("Gauge", () => {
  it("renders every variant in jsdom", () => {
    for (const variant of ["zones", "diverging", "band"] as const) {
      const { root } = renderGauge({ variant, pct: 0.4, band: { start: 0.2, end: 0.6 } });
      expect(root.tagName).toBe("DIV");
      expect(root.style.height).toBe("10px");
    }
  });

  it("places the knob at pct within range", () => {
    expect(knobLeft({ variant: "zones", pct: 0 })).toBe("0%");
    expect(knobLeft({ variant: "zones", pct: 0.25 })).toBe("25%");
    expect(knobLeft({ variant: "diverging", pct: 0.5 })).toBe("50%");
    expect(knobLeft({ variant: "zones", pct: 1 })).toBe("100%");
  });

  it("clamps pct below 0 to the left end and above 1 to the right end", () => {
    expect(knobLeft({ variant: "zones", pct: -0.4 })).toBe("0%");
    expect(knobLeft({ variant: "diverging", pct: -12 })).toBe("0%");
    expect(knobLeft({ variant: "zones", pct: 1.7 })).toBe("100%");
    expect(knobLeft({ variant: "band", pct: 42, band: { start: 0.2, end: 0.6 } })).toBe("100%");
  });

  it("clamps the band highlight to the track", () => {
    const inRange = renderGauge({ variant: "band", pct: 0.5, band: { start: 0.25, end: 0.75 } });
    expect(inRange.children).toHaveLength(2);
    expect(inRange.children[0].style.left).toBe("25%");
    expect(inRange.children[0].style.width).toBe("50%");

    const outOfRange = renderGauge({ variant: "band", pct: 0.5, band: { start: -0.5, end: 2 } });
    expect(outOfRange.children[0].style.left).toBe("0%");
    expect(outOfRange.children[0].style.width).toBe("100%");
  });

  it("omits the knob when showMarker is false", () => {
    const { children } = renderGauge({ variant: "zones", pct: 0.5, showMarker: false });
    expect(children).toHaveLength(0);
  });

  it("sizes the knob from height (non-band) and draws a 2px tick for band", () => {
    const zones = renderGauge({ variant: "zones", pct: 0.5, height: 12 });
    expect(zones.children[0].style.width).toBe("16px");
    expect(zones.children[0].style.height).toBe("16px");

    const band = renderGauge({ variant: "band", pct: 0.5, height: 12 });
    expect(band.children[0].style.width).toBe("2px");
  });
});
