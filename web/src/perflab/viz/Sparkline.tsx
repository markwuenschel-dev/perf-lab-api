// Compact standalone sparkline (no axes) for stat tiles and inline trends. Draws
// its own <svg> — it is not a Chart child, so it can live inside a tile. Area wash
// + 2px line + optional end marker with a surface ring.
import { useId } from "react";
import { useVizTheme } from "./useVizTheme";
import { linePath, areaPath, type Vec2 } from "./scales";

export interface SparklineProps {
  values: readonly number[];
  width?: number;
  height?: number;
  color?: string;
  /** Draw the area wash under the line. Default true. */
  fill?: boolean;
  /** Draw the end-point marker. Default true. */
  marker?: boolean;
  /** Fixed domain; defaults to the values' own min/max (padded). */
  min?: number;
  max?: number;
  className?: string;
}

export function Sparkline({ values, width = 120, height = 32, color, fill = true, marker = true, min, max, className }: SparklineProps) {
  const { chrome, accent } = useVizTheme();
  const id = useId().replace(/:/g, "");
  if (values.length === 0) return null;
  const c = color ?? accent;
  const lo = min ?? Math.min(...values);
  const hi = max ?? Math.max(...values);
  const span = hi - lo || 1;
  const pad = 3;
  const n = values.length;
  const pts: Vec2[] = values.map((v, i) => [
    pad + (n === 1 ? 0 : (i / (n - 1)) * (width - 2 * pad)),
    pad + (1 - (v - lo) / span) * (height - 2 * pad),
  ]);
  const end = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className={className} aria-hidden="true">
      {fill && (
        <>
          <defs>
            <linearGradient id={`spark-${id}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={c} stopOpacity={0.16} />
              <stop offset="100%" stopColor={c} stopOpacity={0} />
            </linearGradient>
          </defs>
          <path d={areaPath(pts, height - pad)} fill={`url(#spark-${id})`} />
        </>
      )}
      <path d={linePath(pts)} fill="none" stroke={c} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      {marker && <circle cx={end[0]} cy={end[1]} r={2.6} fill={c} stroke={chrome.gap} strokeWidth={1.5} />}
    </svg>
  );
}
