// Column marks: <=24px thick, 4px rounded data-end (square at the baseline),
// separated by a 2px surface gap. Color follows the ENTITY, never render order:
//   - "categorical": each datum's stable key → a fixed categorical slot
//   - "series":      one hue (single-variable-over-time); optional emphasisKey
//                    paints the highlighted column in the accent, rest de-emphasised
import { useChart } from "./chartContext";
import { bandScale } from "./scales";

export interface BarDatum {
  /** Stable identity — decides the categorical slot (never the array index). */
  key: string;
  label?: string;
  value: number;
}

export interface BarsProps {
  data: readonly BarDatum[];
  /** How color is assigned. Default "series". */
  color?: "categorical" | "series";
  /** For "categorical": explicit key→slot map (else slot = order encountered). */
  slotOf?: Record<string, number>;
  /** For "series": the single hue. Defaults to categorical slot 1. */
  baseColor?: string;
  /** For "series": key to highlight in the accent (emphasis form). */
  emphasisKey?: string;
  /** Max column thickness in px. Default 24. */
  maxBarWidth?: number;
  /** Rounded data-end radius in px. Default 4. */
  radius?: number;
  /** Inner padding fraction between columns (the 2px gap grows from this). */
  innerPad?: number;
}

/** Top-rounded, bottom-square column path. */
function columnPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.min(r, w / 2, h);
  if (h <= 0) return "";
  return (
    `M${x},${y + h}` +
    `L${x},${y + rr}` +
    `Q${x},${y} ${x + rr},${y}` +
    `L${x + w - rr},${y}` +
    `Q${x + w},${y} ${x + w},${y + rr}` +
    `L${x + w},${y + h}Z`
  );
}

export function Bars({
  data,
  color = "series",
  slotOf,
  baseColor,
  emphasisKey,
  maxBarWidth = 24,
  radius = 4,
  innerPad = 0.34,
}: BarsProps) {
  const { yScale, plot, colors, accent, chrome } = useChart();
  if (!yScale || data.length === 0) return null;
  const band = bandScale({ count: data.length, range: [plot.x, plot.x + plot.w], innerPad });
  const w = Math.min(band.bandWidth, maxBarWidth);
  const y0 = yScale(yScale.domain[0]); // baseline pixel

  const fillFor = (d: BarDatum, i: number): string => {
    if (color === "categorical") {
      const slot = slotOf?.[d.key] ?? i;
      return colors.categorical[slot % colors.categorical.length];
    }
    if (emphasisKey) return d.key === emphasisKey ? accent : chrome.baseline;
    return baseColor ?? colors.categorical[0];
  };

  return (
    <g>
      {data.map((d, i) => {
        const cx = band.center(i);
        const top = yScale(d.value);
        const h = Math.abs(y0 - top);
        const x = cx - w / 2;
        return <path key={d.key} d={columnPath(x, Math.min(top, y0), w, h, radius)} fill={fillFor(d, i)} />;
      })}
    </g>
  );
}
