// -----------------------------------------------------------------------------
// Perf Lab data-visualization palette — THE authored source of truth.
//
// This is the ONLY place palette hexes are edited. Everything else is generated:
//   - `pnpm tokens`        → src/perflab/viz/tokens.ts + src/viz-tokens.generated.css
//   - `pnpm tokens:check`  → fails CI if the generated files drift, and validates
//                            every ramp with scripts/validate_palette.js
//
// Every ramp here has been run through the org data-viz validator against the
// app's real chart surface (dark tile #111419, light tile #ffffff). See the
// per-ramp notes for the validator verdict. Re-run `pnpm tokens:check` after any
// edit and fix any hard FAIL before committing.
//
// Design rules encoded here (see docs / the dataviz skill):
//   • CATEGORICAL is a fixed 8-slot order, assigned by entity identity, never
//     cycled. It is separate from the brand accent.
//   • The user-selectable brand accent (--ac, default lime) is the EMPHASIS
//     channel (single-series / "current" marks) and is never a categorical slot.
//   • STATUS (good/warn/serious/critical) is reserved for state and never reused
//     as a series color.
//   • SEQUENTIAL is one hue light→dark (magnitude); DIVERGING is two hues + a
//     neutral gray midpoint (polarity).
// -----------------------------------------------------------------------------

/** Chart surfaces the validator measures contrast against, per mode. */
export const SURFACES = {
  dark: { canvas: "#06070a", panel: "#0c0e13", surface: "#0e1116", tile: "#111419" },
  light: { canvas: "#f6f6f4", panel: "#fbfbf9", surface: "#ffffff", tile: "#ffffff" },
};

/** Text/ink ramp per mode (labels, values, axis text — never a series color). */
export const TEXT = {
  dark: { ink: "#eef0f3", soft: "#cfd4dd", mute: "#9aa0ab", faint: "#6a7180", dim: "#565d69" },
  light: { ink: "#14171c", soft: "#3d434e", mute: "#5b616c", faint: "#8b909b", dim: "#b0b5be" },
};

// Categorical — 8 slots, fixed order. Validator (adjacent CVD, chroma, band,
// contrast): dark PASS (worst adjacent CVD ΔE 10.3 — floor band, relies on the
// mandatory secondary encoding: legend + direct labels + 2px surface gaps);
// light PASS (3 slots sub-3:1 vs white → relief rule: visible labels / table view).
export const CATEGORICAL = {
  dark: ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"],
  light: ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"],
};

// Sequential — one teal-family hue light→dark (tissue-load body-map, single-
// variable intensity). Validator (ordinal): both modes PASS — monotone L, single
// hue (~4°), light end clears the 2:1 floor vs surface.
export const SEQUENTIAL = {
  dark: ["#bfeee1", "#8fddc9", "#5fc9ad", "#33ac8f", "#1f8a71", "#136350"],
  light: ["#5cbfa4", "#3ba888", "#288f72", "#1c745b", "#125844", "#0a3d30"],
};

// Diverging — blue ↔ orange poles with a NEUTRAL GRAY midpoint (FieldTest
// speed↔endurance gauge, any Δ-to-baseline bar). Poles reuse categorical blue /
// orange; the midpoint is a true gray so "zero" reads as nothing.
export const DIVERGING = {
  dark: { from: "#3987e5", mid: "#383835", to: "#d95926" },
  light: { from: "#2a78d6", mid: "#e9e7e2", to: "#eb6834" },
};

// Status — RESERVED (readiness/fatigue thresholds, load status, RPE dots). Never
// a series color. Each ships with an icon + label so it never carries meaning by
// color alone (mitigates the sub-3:1 relief cases: light `warn` 2.80:1).
// Contrast vs surface — dark: good 9.56 · warn 11.33 · serious 7.95 · critical 5.29;
// light: good 3.35 · warn 2.80 · serious 3.47 · critical 4.80.
export const STATUS = {
  dark: { good: "#5fd08a", warn: "#f5c451", serious: "#ff8a5c", critical: "#ef5350" },
  light: { good: "#0ca30c", warn: "#d68a00", serious: "#e2632f", critical: "#d03b3b" },
};

// Brand identity constants — the EXACT current values, kept verbatim so the legacy
// `COLORS` shim (src/perflab/sim.ts) stays pixel-identical until each screen is
// migrated onto the viz layer. `lime` is the default accent (--ac is user-set at
// runtime); teal/mint/info are secondary brand hues. `hot` == STATUS.dark.serious.
export const BRAND = {
  lime: "#c6f135", teal: "#7bd6c0", mint: "#45d6c4", info: "#86b8ff",
  good: "#5fd08a", warn: "#f5c451", hot: "#ff8a5c",
  ink: "#eef0f3", soft: "#cfd4dd", mute: "#9aa0ab", faint: "#646b78", dim: "#565d69",
};

/** Assemble the per-mode palette object the app consumes (via generated tokens.ts). */
export function paletteFor(mode) {
  return {
    surface: SURFACES[mode],
    text: TEXT[mode],
    categorical: CATEGORICAL[mode],
    sequential: SEQUENTIAL[mode],
    diverging: DIVERGING[mode],
    status: STATUS[mode],
  };
}

export const MODES = ["light", "dark"];
