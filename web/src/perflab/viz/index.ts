// Public entry for the viz layer. Screens import primitives from here.
// (Re-export only — no local declarations — so tree-shaking stays intact.)
export { PALETTE, COLORS } from "./tokens";
export type { Mode, Palette } from "./tokens";
export { useVizTheme } from "./useVizTheme";
export type { VizTheme } from "./useVizTheme";
export { useChart } from "./chartContext";
export type { ChartCtx, PlotRect } from "./chartContext";
export { Chart } from "./Chart";
export type { ChartProps } from "./Chart";
export {
  linearScale,
  bandScale,
  radial,
  linePath,
  areaPath,
  niceTicks,
  compact,
} from "./scales";
export type { LinearScale, BandScale, Radial, Vec2 } from "./scales";
