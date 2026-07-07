// A data-point dot with a 2px surface ring so it stays legible where it crosses
// a line or another mark. r >= 4 → >= 8px diameter per the mark spec.
import { useChart } from "./chartContext";

export interface MarkerProps {
  /** Data-space coordinates. */
  x: number;
  y: number;
  color?: string;
  /** Radius in px. Default 4 (8px dot). */
  r?: number;
  /** Draw the 2px surface ring. Default true. */
  ring?: boolean;
}

export function Marker({ x, y, color, r = 4, ring = true }: MarkerProps) {
  const { xScale, yScale, colors, chrome } = useChart();
  const cx = xScale ? xScale(x) : x;
  const cy = yScale ? yScale(y) : y;
  return (
    <circle
      cx={cx}
      cy={cy}
      r={r}
      fill={color ?? colors.categorical[0]}
      stroke={ring ? chrome.gap : "none"}
      strokeWidth={ring ? 2 : 0}
      vectorEffect="non-scaling-stroke"
    />
  );
}
