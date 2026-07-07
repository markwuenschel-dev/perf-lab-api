// Resolves the CSS-level truth (theme mode + runtime accent) into concrete values
// the SVG layer can use. SVG `fill`/`stroke`/gradient stops want real hex, not a
// `var(--ac)` reference (which is awkward inside <linearGradient>), so every chart
// reads its colors through this hook rather than touching `COLORS`, `var(--ac)`,
// or hard-coded literals directly.
import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { PALETTE, COLORS, type Mode, type Palette } from "./tokens";
import { chrome, type Chrome } from "./chrome";

export interface VizTheme {
  mode: Mode;
  /** The resolved palette for the current mode (concrete hexes). */
  colors: Palette;
  /** Non-series chrome (gridlines, gap/ring, track) for the current mode. */
  chrome: Chrome;
  /** The user-selectable brand accent (`--ac`) — the emphasis channel. */
  accent: string;
}

function readAccent(): string {
  if (typeof document === "undefined") return COLORS.lime;
  const v = getComputedStyle(document.documentElement).getPropertyValue("--ac").trim();
  return v || COLORS.lime;
}

export function useVizTheme(): VizTheme {
  // next-themes drives the mode once the ThemeProvider is mounted (light-mode
  // phase). Until then there is no provider and `resolvedTheme` is undefined, so
  // this safely resolves to "dark" — the app's current single theme.
  const { resolvedTheme } = useTheme();
  const mode: Mode = resolvedTheme === "light" ? "light" : "dark";

  // The accent is written to `document.documentElement.style` at runtime by
  // PerfLabProvider when the user changes it in Settings. Watch the style
  // attribute so charts recolor their emphasis marks immediately.
  const [accent, setAccent] = useState<string>(readAccent);
  useEffect(() => {
    if (typeof document === "undefined") return;
    const html = document.documentElement;
    const sync = () => setAccent(readAccent());
    sync();
    const obs = new MutationObserver(sync);
    obs.observe(html, { attributes: true, attributeFilter: ["style", "class"] });
    return () => obs.disconnect();
  }, []);

  return { mode, colors: PALETTE[mode], chrome: chrome(mode), accent };
}
