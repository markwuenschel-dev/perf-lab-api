// The chart frame. Standardises the <svg> every hand-rolled chart used to open
// by hand: a viewBox, responsive scaling, non-scaling strokes, tabular figures,
// and a context carrying the plot rect + resolved theme down to the marks.
//
// Enforces the single-axis rule structurally — a Chart owns exactly one plot
// rect; there is no seam for a second y-scale. Two measures of different scale
// become two stacked <Chart>s (small multiples), not one dual-axis chart.
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useVizTheme } from "./useVizTheme";
import { ChartContext, type ChartCtx, type PlotRect } from "./chartContext";

type Padding = number | { top?: number; right?: number; bottom?: number; left?: number };

function resolvePadding(p: Padding): Required<Exclude<Padding, number>> {
  if (typeof p === "number") return { top: p, right: p, bottom: p, left: p };
  return { top: p.top ?? 0, right: p.right ?? 0, bottom: p.bottom ?? 0, left: p.left ?? 0 };
}

export interface ChartProps {
  /** viewBox width in abstract units. */
  width: number;
  /** viewBox height in abstract units. */
  height: number;
  /** Inner padding reserved for axes/labels (number or per-side). Default 0. */
  padding?: Padding;
  /** Accessible name — required; a chart without one is unreadable to AT. */
  ariaLabel: string;
  className?: string;
  /** Marks (Axis/Bars/Line/Area/…) that consume the chart context. */
  children: ReactNode;
  /** Extra defs (gradients) if a mark needs them at the svg root. */
  defs?: ReactNode;
}

export function Chart({ width, height, padding = 0, ariaLabel, className, children, defs }: ChartProps) {
  const { colors, mode, accent } = useVizTheme();
  const pad = resolvePadding(padding);
  const plot: PlotRect = {
    x: pad.left,
    y: pad.top,
    w: Math.max(0, width - pad.left - pad.right),
    h: Math.max(0, height - pad.top - pad.bottom),
  };
  const ctx: ChartCtx = { width, height, plot, colors, mode, accent };
  return (
    <ChartContext.Provider value={ctx}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={ariaLabel}
        className={cn("block h-auto w-full overflow-visible", className)}
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        {defs && <defs>{defs}</defs>}
        {children}
      </svg>
    </ChartContext.Provider>
  );
}
