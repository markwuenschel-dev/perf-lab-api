// Chart frame context — the plot geometry + resolved theme every mark reads.
// Kept in its own (component-free) module so Chart.tsx can export ONLY the
// component and stay Fast-Refresh clean.
import { createContext, useContext } from "react";
import type { Mode, Palette } from "./tokens";

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
  mode: Mode;
  /** Runtime brand accent (emphasis channel). */
  accent: string;
}

export const ChartContext = createContext<ChartCtx | null>(null);

/** Read the enclosing <Chart> geometry + theme. Throws if used outside a Chart. */
export function useChart(): ChartCtx {
  const ctx = useContext(ChartContext);
  if (!ctx) throw new Error("viz marks must be rendered inside a <Chart>");
  return ctx;
}
