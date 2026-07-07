// Marker-on-a-track gauges — consolidates the three "a knob on a bar" widgets
// that were each hand-rolled: the SessionPlayer HR gauge (`zones`), the FieldTest
// speed↔endurance bipolar meter (`diverging`, two poles + neutral gray midpoint),
// and the Overview ACWR sweet-spot meter (`band`, a highlighted safe region).
// (Design note: these live here rather than as Meter variants because a Meter is a
// label·track·value row, whereas these are positional gauges.)
import { useVizTheme } from "./useVizTheme";
import { cn } from "@/lib/utils";

export interface GaugeStop {
  /** 0–1 position along the track. */
  offset: number;
  color: string;
}

export interface GaugeProps {
  variant: "zones" | "diverging" | "band";
  /** Marker position 0–1 across the track (diverging: 0.5 = center). */
  pct: number;
  height?: number;
  className?: string;
  /** `zones`: gradient stops (defaults to a good→critical status ramp). */
  stops?: readonly GaugeStop[];
  /** `band`: highlighted safe region [start, end] in 0–1. */
  band?: { start: number; end: number };
  /** Marker color. Defaults to primary ink. */
  markerColor?: string;
  /** Hide the marker knob (e.g. no value yet). Default true. */
  showMarker?: boolean;
}

const clamp01 = (n: number) => Math.max(0, Math.min(1, n));

export function Gauge({ variant, pct, height = 10, className, stops, band, markerColor, showMarker = true }: GaugeProps) {
  const { colors, chrome, accent } = useVizTheme();
  const pos = clamp01(pct) * 100;
  const knob = markerColor ?? colors.text.ink;

  let background: string;
  if (variant === "diverging") {
    const d = colors.diverging;
    background = `linear-gradient(90deg, ${d.from} 0%, ${d.mid} 50%, ${d.to} 100%)`;
  } else if (variant === "zones") {
    const s = stops ?? [
      { offset: 0, color: colors.status.good },
      { offset: 0.5, color: colors.status.warn },
      { offset: 0.75, color: colors.status.serious },
      { offset: 1, color: colors.status.critical },
    ];
    background = `linear-gradient(90deg, ${s.map((st) => `${st.color} ${clamp01(st.offset) * 100}%`).join(", ")})`;
  } else {
    background = chrome.track;
  }

  return (
    <div className={cn("relative w-full overflow-visible rounded-full", className)} style={{ height, background }}>
      {variant === "band" && band && (
        <div
          className="absolute inset-y-0 rounded-full"
          style={{
            left: `${clamp01(band.start) * 100}%`,
            width: `${clamp01(band.end - band.start) * 100}%`,
            background: `color-mix(in srgb, ${colors.status.good} 28%, transparent)`,
          }}
        />
      )}
      {/* marker knob */}
      {showMarker && (
      <div
        className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full"
        style={{
          left: `${pos}%`,
          width: variant === "band" ? 2 : height + 4,
          height: height + 4,
          background: variant === "band" ? accent : knob,
          border: variant === "band" ? "none" : `2px solid ${chrome.gap}`,
        }}
      />
      )}
    </div>
  );
}
