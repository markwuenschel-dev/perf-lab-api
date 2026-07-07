// Radar / spider chart — the Twin capacity view. Standalone svg (non-cartesian,
// so not a Chart child) built on scales.radial(). Draws grid rings + spokes, an
// optional dashed baseline polygon (e.g. block-start), the current value polygon
// (accent wash), vertex markers, and axis labels in text tokens.
import { useVizTheme } from "./useVizTheme";
import { radial } from "./scales";

export interface RadarAxis {
  key: string;
  label: string;
  value: number;
  /** Max for this axis (value/max → 0…1 radius fraction). */
  max: number;
}

export interface RadarProps {
  axes: readonly RadarAxis[];
  /** Optional comparison polygon (same axis order), e.g. block start. */
  baseline?: readonly number[];
  /** Square viewBox size in px. Default 240. */
  size?: number;
  /** Number of concentric grid rings. Default 4. */
  levels?: number;
  /** Value polygon color. Defaults to the accent (the "now" emphasis). */
  color?: string;
  className?: string;
}

export function Radar({ axes, baseline, size = 240, levels = 4, color, className }: RadarProps) {
  const { accent, chrome, colors } = useVizTheme();
  const c = color ?? accent;
  const n = axes.length;
  if (n < 3) return null;
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 34; // room for labels
  const geo = radial({ cx, cy, r, count: n });
  const frac = axes.map((a) => Math.max(0, Math.min(1, a.max ? a.value / a.max : 0)));

  return (
    <svg viewBox={`0 0 ${size} ${size}`} className={className} role="img" aria-label="Capacity radar">
      {/* grid rings */}
      {Array.from({ length: levels }, (_, l) => (
        <polygon
          key={`ring${l}`}
          points={geo.gridPolygon((l + 1) / levels)}
          fill="none"
          stroke={chrome.gridline}
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
      ))}
      {/* spokes */}
      {axes.map((a, i) => {
        const [sx, sy] = geo.spoke(i);
        return <line key={`spoke${a.key}`} x1={cx} y1={cy} x2={sx} y2={sy} stroke={chrome.gridline} strokeWidth={1} vectorEffect="non-scaling-stroke" />;
      })}
      {/* baseline polygon (dashed) */}
      {baseline && (
        <polygon
          points={geo.valuePolygon(baseline.map((v, i) => Math.max(0, Math.min(1, axes[i]?.max ? v / axes[i].max : v))))}
          fill="none"
          stroke={colors.text.mute}
          strokeWidth={1.5}
          strokeDasharray="4 3"
          vectorEffect="non-scaling-stroke"
        />
      )}
      {/* value polygon */}
      <polygon points={geo.valuePolygon(frac)} fill={c} fillOpacity={0.16} stroke={c} strokeWidth={2} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      {/* vertex markers */}
      {frac.map((f, i) => {
        const [px, py] = geo.point(i, r * f);
        return <circle key={`v${axes[i].key}`} cx={px} cy={py} r={3} fill={c} stroke={chrome.gap} strokeWidth={1.5} />;
      })}
      {/* axis labels */}
      {axes.map((a, i) => {
        const [lx, ly] = geo.point(i, r + 16);
        const anchor = Math.abs(lx - cx) < 4 ? "middle" : lx > cx ? "start" : "end";
        return (
          <text key={`lbl${a.key}`} x={lx} y={ly} textAnchor={anchor} dominantBaseline="central" fontSize={10} fill={colors.text.mute}>
            {a.label}
          </text>
        );
      })}
    </svg>
  );
}
