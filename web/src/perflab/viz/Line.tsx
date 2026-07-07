// A 2px line mark. Reads the Chart's scales; data is in data-space [x, y] pairs.
import { useChart } from "./chartContext";
import { linePath, type Vec2 } from "./scales";

export interface LineProps {
  /** Data-space points [x, y]. */
  data: readonly Vec2[];
  /** Stroke color. Single-series marks pass the accent; defaults to slot 1. */
  color?: string;
  /** Stroke width in px (non-scaling). Default 2 per the mark spec. */
  width?: number;
  dashed?: boolean;
  opacity?: number;
}

export function Line({ data, color, width = 2, dashed, opacity = 1 }: LineProps) {
  const { xScale, yScale, colors } = useChart();
  if (!xScale || !yScale || data.length === 0) return null;
  const pts: Vec2[] = data.map(([x, y]) => [xScale(x), yScale(y)]);
  return (
    <path
      d={linePath(pts)}
      fill="none"
      stroke={color ?? colors.categorical[0]}
      strokeWidth={width}
      strokeLinejoin="round"
      strokeLinecap="round"
      strokeDasharray={dashed ? "4 4" : undefined}
      vectorEffect="non-scaling-stroke"
      opacity={opacity}
    />
  );
}
