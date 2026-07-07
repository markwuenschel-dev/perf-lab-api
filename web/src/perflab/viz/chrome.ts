// Non-series "chrome" colors — gridlines, baselines, the surface gap/ring that
// separates touching marks, and the meter track. These are derived per mode (not
// part of the generated data palette, which is series colors only). Kept in one
// place so light mode restyles chrome in a single edit.
import type { Mode } from "./tokens";

export interface Chrome {
  /** Hairline gridlines — one step off the surface, recessive. */
  gridline: string;
  /** Baseline / axis line — slightly stronger than gridlines. */
  baseline: string;
  /** The chart surface behind marks: the 2px gap between touching bars and the
   *  ring around dots. Matches the card surface so it reads as negative space. */
  gap: string;
  /** Unfilled meter/ring track (the pre-viz value was rgba(255,255,255,.07)). */
  track: string;
}

const DARK: Chrome = {
  gridline: "rgba(255,255,255,0.07)",
  baseline: "rgba(255,255,255,0.14)",
  gap: "#111419",
  track: "rgba(255,255,255,0.07)",
};

const LIGHT: Chrome = {
  gridline: "rgba(0,0,0,0.07)",
  baseline: "rgba(0,0,0,0.16)",
  gap: "#ffffff",
  track: "rgba(0,0,0,0.06)",
};

export function chrome(mode: Mode): Chrome {
  return mode === "dark" ? DARK : LIGHT;
}
