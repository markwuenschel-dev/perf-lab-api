// A 2px line mark. Reads the Chart's scales; data is in data-space [x, y] pairs.
import { useEffect, useId } from "react";
import { useChart } from "./chartContext";
import { linePath, type Vec2 } from "./scales";

export interface LineProps {
  /** Data-space points [x, y]. */
  data: readonly Vec2[];
  /** Stroke color. Single-series marks pass the accent; defaults to slot 1. */
  color?: string;
  /** Series name for the shared tooltip/legend. */
  label?: string;
  /** Stroke width in px (non-scaling). Default 2 per the mark spec. */
  width?: number;
  dashed?: boolean;
  opacity?: number;
}

export function Line({ data, color, label, width = 2, dashed, opacity = 1 }: LineProps) {
  const { xScale, yScale, colors, register, unregister } = useChart();
  const id = useId();
  const c = color ?? colors.categorical[0];
  useEffect(() => {
    register?.({ id, label: label ?? "value", color: c, points: data });
    return () => unregister?.(id);
  }, [register, unregister, id, label, c, data]);
  if (!xScale || !yScale || data.length === 0) return null;
  const pts: Vec2[] = data.map(([x, y]) => [xScale(x), yScale(y)]);
  return (
    <path
      d={linePath(pts)}
      fill="none"
      stroke={c}
      strokeWidth={width}
      strokeLinejoin="round"
      strokeLinecap="round"
      strokeDasharray={dashed ? "4 4" : undefined}
      vectorEffect="non-scaling-stroke"
      opacity={opacity}
    />
  );
}
