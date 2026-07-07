// Anatomical tissue-load heatmap — a fixed-coordinate silhouette whose joints are
// shaded by load. Tissue load is one-directional magnitude, so color comes from
// the SEQUENTIAL hue (not the status ramp): prominence rises with load via a
// single-hue opacity/strength encoding that reads in both modes.
import { useVizTheme } from "./useVizTheme";

/** Joint positions in the 140×240 viewBox. Symmetric joints get two dots. */
const JOINTS: Record<string, ReadonlyArray<readonly [number, number]>> = {
  Shoulder: [[50, 66], [90, 66]],
  Elbow: [[41, 104], [99, 104]],
  Wrist: [[37, 139], [103, 139]],
  Finger: [[35, 161], [105, 161]],
  Lumbar: [[70, 108]],
  Hip: [[58, 131], [82, 131]],
  Knee: [[58, 180], [82, 180]],
  Ankle: [[58, 221], [82, 221]],
};

export interface BodyMapHeatmapProps {
  /** Region key (matches JOINTS) → load value. */
  regions: Record<string, number>;
  /** Value that maps to full load. Default 100. */
  max?: number;
  /** Dot radius in px. Default 8. */
  radius?: number;
  className?: string;
  /** Called on hover/focus of a region (for the Phase interaction layer). */
  onRegion?: (key: string, value: number) => void;
}

export function BodyMapHeatmap({ regions, max = 100, radius = 8, className, onRegion }: BodyMapHeatmapProps) {
  const { colors, chrome } = useVizTheme();
  const seq = colors.sequential;
  const strong = seq[seq.length - 1];
  const silhouette = chrome.track;

  return (
    <svg viewBox="0 0 140 240" className={className} role="img" aria-label="Tissue load by body region">
      {/* faint silhouette */}
      <g fill={silhouette}>
        <circle cx={70} cy={22} r={14} />
        <rect x={52} y={40} width={36} height={72} rx={14} />
        <rect x={38} y={44} width={12} height={96} rx={6} />
        <rect x={90} y={44} width={12} height={96} rx={6} />
        <rect x={54} y={118} width={13} height={104} rx={6} />
        <rect x={73} y={118} width={13} height={104} rx={6} />
      </g>
      {/* joints */}
      {Object.entries(JOINTS).map(([key, pts]) => {
        const v = regions[key] ?? 0;
        const frac = Math.max(0, Math.min(1, v / max));
        const opacity = 0.2 + 0.7 * frac;
        return pts.map(([cx, cy], i) => (
          <circle
            key={`${key}-${i}`}
            cx={cx}
            cy={cy}
            r={radius}
            fill={strong}
            fillOpacity={opacity}
            stroke={strong}
            strokeOpacity={0.9}
            strokeWidth={1.5}
            tabIndex={onRegion ? 0 : undefined}
            onMouseEnter={onRegion ? () => onRegion(key, v) : undefined}
            onFocus={onRegion ? () => onRegion(key, v) : undefined}
            style={onRegion ? { cursor: "pointer" } : undefined}
          />
        ));
      })}
    </svg>
  );
}
