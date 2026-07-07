// The chart frame. Standardises the <svg> every hand-rolled chart used to open
// by hand: a viewBox, responsive scaling, non-scaling strokes, tabular figures,
// and a context carrying the plot rect + resolved theme down to the marks.
//
// Enforces the single-axis rule structurally — a Chart owns exactly one plot
// rect; there is no seam for a second y-scale. Two measures of different scale
// become two stacked <Chart>s (small multiples), not one dual-axis chart.
//
// When it has an x scale and at least one Line/Area registers, the Chart also
// drives ONE shared crosshair + tooltip across every series (the reader aims at
// an x, never at a 2px line). Same details on keyboard focus as on hover.
import { useCallback, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useVizTheme } from "./useVizTheme";
import { linearScale } from "./scales";
import { Tooltip, type TooltipRow } from "./Tooltip";
import { ChartContext, type ChartCtx, type PlotRect, type SeriesReg } from "./chartContext";

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
  /** Data domain for the x axis [lo, hi]. Marks read the derived scale. */
  xDomain?: readonly [number, number];
  /** Data domain for the (single) y axis [lo, hi]. */
  yDomain?: readonly [number, number];
  /** Disable the shared crosshair + tooltip (on by default when there's an x scale). */
  interactive?: boolean;
  /** Tooltip title for the hovered x index (e.g. a date). Omit for none. */
  formatX?: (index: number) => string;
  /** Format a series value for the tooltip. Default: rounded number. */
  formatValue?: (value: number) => string;
  className?: string;
  /** Marks (Axis/Bars/Line/Area/…) that consume the chart context. */
  children: ReactNode;
  /** Extra defs (gradients) if a mark needs them at the svg root. */
  defs?: ReactNode;
}

export function Chart({
  width,
  height,
  padding = 0,
  ariaLabel,
  xDomain,
  yDomain,
  interactive = true,
  formatX,
  formatValue = (v) => `${Math.round(v)}`,
  className,
  children,
  defs,
}: ChartProps) {
  const { colors, chrome, mode, accent } = useVizTheme();
  const pad = resolvePadding(padding);
  const plot: PlotRect = {
    x: pad.left,
    y: pad.top,
    w: Math.max(0, width - pad.left - pad.right),
    h: Math.max(0, height - pad.top - pad.bottom),
  };
  const xScale = xDomain ? linearScale({ domain: xDomain, range: [plot.x, plot.x + plot.w] }) : undefined;
  const yScale = yDomain ? linearScale({ domain: yDomain, range: [plot.y + plot.h, plot.y] }) : undefined;

  const svgRef = useRef<SVGSVGElement>(null);
  const series = useRef<Map<string, SeriesReg>>(new Map());
  const register = useCallback((s: SeriesReg) => { series.current.set(s.id, s); }, []);
  const unregister = useCallback((id: string) => { series.current.delete(id); }, []);
  const [hover, setHover] = useState<number | null>(null);

  const enabled = interactive && !!xScale;

  const moveTo = useCallback((clientX: number) => {
    const svg = svgRef.current;
    if (!svg || !xScale) return;
    const rect = svg.getBoundingClientRect();
    if (rect.width === 0) return;
    const px = ((clientX - rect.left) / rect.width) * width;
    const dataX = xScale.invert(px);
    const first = series.current.values().next().value as SeriesReg | undefined;
    if (!first || first.points.length === 0) return;
    let ni = 0, best = Infinity;
    first.points.forEach((p, i) => { const d = Math.abs(p[0] - dataX); if (d < best) { best = d; ni = i; } });
    setHover(ni);
  }, [xScale, width]);

  const onMove = (e: ReactPointerEvent) => { if (enabled) moveTo(e.clientX); };
  const onLeave = () => setHover(null);

  const ctx: ChartCtx = {
    width, height, plot, colors, chrome, mode, accent, xScale, yScale,
    register: enabled ? register : undefined,
    unregister: enabled ? unregister : undefined,
  };

  // Resolve the crosshair + tooltip for the hovered index.
  const regs = [...series.current.values()];
  const hi = hover;
  const canShow = enabled && hi != null && xScale != null && yScale != null && regs.length > 0 && regs[0].points[hi] != null;
  const crossX = canShow ? xScale!(regs[0].points[hi!][0]) : 0;
  const rows: TooltipRow[] = canShow
    ? regs.filter((s) => s.points[hi!]).map((s) => ({ label: s.label, value: formatValue(s.points[hi!][1]), color: s.color }))
    : [];

  return (
    <div className={cn("relative", className)}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={ariaLabel}
        className="block h-auto w-full overflow-visible"
        style={{ fontVariantNumeric: "tabular-nums", touchAction: "none" }}
        onPointerMove={onMove}
        onPointerLeave={onLeave}
        onPointerDown={onMove}
      >
        {defs && <defs>{defs}</defs>}
        <ChartContext.Provider value={ctx}>{children}</ChartContext.Provider>
        {canShow && (
          <g pointerEvents="none">
            <line x1={crossX} x2={crossX} y1={plot.y} y2={plot.y + plot.h} stroke={chrome.baseline} strokeWidth={1} vectorEffect="non-scaling-stroke" />
            {regs.map((s) => s.points[hi!] && (
              <circle key={s.id} cx={xScale!(s.points[hi!][0])} cy={yScale!(s.points[hi!][1])} r={3.5} fill={s.color} stroke={chrome.gap} strokeWidth={2} />
            ))}
          </g>
        )}
      </svg>
      {canShow && rows.length > 0 && (
        <Tooltip leftPct={(crossX / width) * 100} title={formatX ? formatX(hi!) : undefined} rows={rows} />
      )}
    </div>
  );
}
