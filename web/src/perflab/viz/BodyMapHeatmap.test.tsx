// @vitest-environment jsdom
//
// BodyMapHeatmap shades each joint dot by load: fill-opacity = 0.2 + 0.7 × frac,
// with frac = value / max clamped to [0, 1]. So opacity is bounded to [0.2, 0.9]
// no matter how far a load over- or undershoots `max`.
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BodyMapHeatmap } from "./BodyMapHeatmap";

afterEach(cleanup);

// cx,cy of the first dot of each joint (left side for symmetric joints).
const FIRST_DOT: Record<string, string> = {
  Shoulder: "50,66",
  Elbow: "41,104",
  Wrist: "37,139",
  Finger: "35,161",
  Lumbar: "70,108",
  Hip: "58,131",
  Knee: "58,180",
  Ankle: "58,221",
};

/** Joint dots are the stroked circles; the silhouette head circle has no stroke. */
function jointDots(container: HTMLElement): SVGCircleElement[] {
  return Array.from(container.querySelectorAll("circle[stroke]")) as SVGCircleElement[];
}

function opacityAt(container: HTMLElement, joint: string): number {
  const [cx, cy] = FIRST_DOT[joint].split(",");
  const dot = jointDots(container).find((c) => c.getAttribute("cx") === cx && c.getAttribute("cy") === cy);
  expect(dot, `dot for ${joint}`).toBeDefined();
  return Number(dot!.getAttribute("fill-opacity"));
}

describe("BodyMapHeatmap", () => {
  it("renders an accessible svg with one dot per joint position", () => {
    const { container } = render(<BodyMapHeatmap regions={{}} />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("role")).toBe("img");
    expect(svg?.getAttribute("aria-label")).toBe("Tissue load by body region");
    // 7 symmetric joints × 2 + Lumbar × 1
    expect(jointDots(container)).toHaveLength(15);
  });

  it("maps in-range load linearly onto opacity", () => {
    const { container } = render(<BodyMapHeatmap regions={{ Knee: 0, Hip: 50, Ankle: 100 }} />);
    expect(opacityAt(container, "Knee")).toBeCloseTo(0.2, 10);
    expect(opacityAt(container, "Hip")).toBeCloseTo(0.55, 10);
    expect(opacityAt(container, "Ankle")).toBeCloseTo(0.9, 10);
  });

  it("clamps the fraction: loads above max cap at 0.9, negative loads floor at 0.2", () => {
    const { container } = render(<BodyMapHeatmap regions={{ Shoulder: 250, Elbow: -40 }} />);
    expect(opacityAt(container, "Shoulder")).toBeCloseTo(0.9, 10);
    expect(opacityAt(container, "Elbow")).toBeCloseTo(0.2, 10);
  });

  it("scales by a custom max and treats missing regions as zero load", () => {
    const { container } = render(<BodyMapHeatmap regions={{ Wrist: 5, Finger: 30 }} max={10} />);
    expect(opacityAt(container, "Wrist")).toBeCloseTo(0.55, 10);
    expect(opacityAt(container, "Finger")).toBeCloseTo(0.9, 10);
    expect(opacityAt(container, "Lumbar")).toBeCloseTo(0.2, 10);
  });

  it("shades both dots of a symmetric joint identically", () => {
    const { container } = render(<BodyMapHeatmap regions={{ Knee: 80 }} />);
    const knees = jointDots(container).filter((c) => c.getAttribute("cy") === "180");
    expect(knees).toHaveLength(2);
    expect(knees[0].getAttribute("fill-opacity")).toBe(knees[1].getAttribute("fill-opacity"));
  });

  it("is focusable and reports the raw region value only when onRegion is given", () => {
    const passive = render(<BodyMapHeatmap regions={{ Hip: 40 }} />);
    expect(jointDots(passive.container).every((c) => !c.hasAttribute("tabindex"))).toBe(true);
    passive.unmount();

    const onRegion = vi.fn();
    const { container } = render(<BodyMapHeatmap regions={{ Hip: 140 }} onRegion={onRegion} />);
    const hip = jointDots(container).find((c) => c.getAttribute("cy") === "131")!;
    expect(hip.getAttribute("tabindex")).toBe("0");
    fireEvent.focus(hip);
    expect(onRegion).toHaveBeenCalledWith("Hip", 140);
  });
});
