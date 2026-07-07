// Chart frame context — the plot geometry + resolved theme every mark reads.
// Kept in its own (component-free) module so Chart.tsx can export ONLY the
// component and stay Fast-Refresh clean.
import { createContext, useContext } from "react";
import type { Mode, Palette } from "./tokens";
import type { Chrome } from "./chrome";
import type { LinearScale, Vec2 } from "./scales";

/** A line/area series registers itself with the Chart so the shared crosshair +
 *  tooltip can read every series' value at the hovered x. */
export interface SeriesReg {
  id: string;
  label: string;
  color: string;
  /** Data-space points [x, y]. */
  points: readonly Vec2[];
}

export interface PlotRect {
  /** Inner plot area, in viewBox units, after padding. */
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ChartCtx {
  /** Full viewBox width/height. */
  width: number;
  height: number;
  /** Inner plot rectangle (where marks draw). */
  plot: PlotRect;
  /** Resolved palette for the active mode. */
  colors: Palette;
  /** Non-series chrome (gridlines, gap/ring, track). */
  chrome: Chrome;
  mode: Mode;
  /** Runtime brand accent (emphasis channel). */
  accent: string;
  /** X scale (data→pixel), built from the Chart's xDomain. Undefined if unset. */
  xScale?: LinearScale;
  /** THE y scale — a Chart owns exactly one (no dual-axis). Undefined if unset. */
  yScale?: LinearScale;
  /** Line/Area marks register here so the Chart can drive one shared crosshair +
   *  tooltip across every series. Undefined when the Chart is non-interactive. */
  register?: (s: SeriesReg) => void;
  unregister?: (id: string) => void;
}

export const ChartContext = createContext<ChartCtx | null>(null);

/** Read the enclosing <Chart> geometry + theme. Throws if used outside a Chart. */
export function useChart(): ChartCtx {
  const ctx = useContext(ChartContext);
  if (!ctx) throw new Error("viz marks must be rendered inside a <Chart>");
  return ctx;
}
