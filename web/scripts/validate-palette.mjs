// -----------------------------------------------------------------------------
// Palette validator runner — the "compute it, don't eyeball it" gate.
//
// Runs the org data-viz checks (scripts/validate_palette.js) over every ramp in
// palette.data.mjs, per mode, against the app's real chart surface:
//   • categorical → adjacent CVD ΔE, chroma floor, lightness band, contrast
//   • sequential  → ordinal (monotone L, single hue, light-end clears surface)
//   • status      → lone-color WCAG contrast vs surface (icon+label relief noted)
//
// Exit 1 only on a hard FAIL (matching the skill's semantics): a CVD floor-band
// WARN and a sub-3:1 contrast WARN are legal WITH secondary encoding, so they do
// not fail the gate — they are surfaced here as reminders.
// -----------------------------------------------------------------------------
import { validate, validateOrdinal, contrast } from "./validate_palette.js";
import { paletteFor, MODES } from "./palette.data.mjs";

const GLYPH = { true: "PASS", false: "FAIL", pass: "PASS", floor: "WARN", fail: "FAIL", relief: "WARN" };
let hardFail = false;

function emit(result) {
  for (const [name, state, detail] of result.report) {
    const g = GLYPH[state] ?? state;
    if (g === "FAIL") hardFail = true;
    console.log(`    [${g.padEnd(4)}] ${name.padEnd(22)} ${detail}`);
  }
}

for (const mode of MODES) {
  const p = paletteFor(mode);
  const surface = p.surface.tile;
  console.log(`\n===== ${mode.toUpperCase()} (chart surface ${surface}) =====`);

  console.log("  categorical:");
  emit(validate([...p.categorical], { mode, surface }));

  console.log("  sequential (ordinal):");
  emit(validateOrdinal([...p.sequential], { mode, surface }));

  console.log("  status (lone-color contrast vs surface; <3:1 ⇒ icon+label relief):");
  for (const [k, v] of Object.entries(p.status)) {
    console.log(`    ${k.padEnd(9)} ${v}  ${contrast(v, surface).toFixed(2)}:1`);
  }
}

console.log(hardFail
  ? "\n✗ palette validation FAILED — fix the marked checks"
  : "\n✓ palette validation passed (WARNs are legal with the mandatory secondary encoding)");
process.exit(hardFail ? 1 : 0);
