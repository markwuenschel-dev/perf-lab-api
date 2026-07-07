// A filled area (~10% wash) with its own line. Each instance mints a unique
// gradient id via useId — fixing the old global-id collision (ovg/rdg/simTraj).
import { useId } from "react";
import { useChart } from "./chartContext";
import { linePath, areaPath, type Vec2 } from "./scales";

export interface AreaProps {
  /** Data-space points [x, y]. */
  data: readonly Vec2[];
  color?: string;
  /** Peak fill opacity at the top of the wash. Default 0.12. */
  fillOpacity?: number;
  /** Draw the 2px line on top of the fill. Default true. */
  line?: boolean;
}

export function Area({ data, color, fillOpacity = 0.12, line = true }: AreaProps) {
  const { xScale, yScale, plot, colors } = useChart();
  const id = useId().replace(/:/g, "");
  if (!xScale || !yScale || data.length === 0) return null;
  const c = color ?? colors.categorical[0];
  const pts: Vec2[] = data.map(([x, y]) => [xScale(x), yScale(y)]);
  const baseline = plot.y + plot.h;
  return (
    <>
      <defs>
        <linearGradient id={`area-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={c} stopOpacity={fillOpacity} />
          <stop offset="100%" stopColor={c} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={areaPath(pts, baseline)} fill={`url(#area-${id})`} />
      {line && (
        <path
          d={linePath(pts)}
          fill="none"
          stroke={c}
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </>
  );
}
