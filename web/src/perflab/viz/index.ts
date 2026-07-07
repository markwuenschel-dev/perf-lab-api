// Public entry for the viz layer. Screens import primitives from here.
// (Re-export only — no local declarations — so tree-shaking stays intact.)
export { PALETTE, COLORS } from "./tokens";
export type { Mode, Palette } from "./tokens";
export { useVizTheme } from "./useVizTheme";
export type { VizTheme } from "./useVizTheme";
export { useChart } from "./chartContext";
export type { ChartCtx, PlotRect } from "./chartContext";
export { chrome } from "./chrome";
export type { Chrome } from "./chrome";
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

// Marks
export { Line } from "./Line";
export type { LineProps } from "./Line";
export { Area } from "./Area";
export type { AreaProps } from "./Area";
export { Marker } from "./Marker";
export type { MarkerProps } from "./Marker";
export { Axis } from "./Axis";
export type { AxisProps } from "./Axis";
export { Bars } from "./Bars";
export type { BarsProps, BarDatum } from "./Bars";
export { Sparkline } from "./Sparkline";
export type { SparklineProps } from "./Sparkline";

// Figures & chrome
export { Ring } from "./Ring";
export type { RingProps } from "./Ring";
export { Meter } from "./Meter";
export type { MeterProps } from "./Meter";
export { StatTile } from "./StatTile";
export type { StatTileProps } from "./StatTile";
export { Legend } from "./Legend";
export type { LegendItem } from "./Legend";
