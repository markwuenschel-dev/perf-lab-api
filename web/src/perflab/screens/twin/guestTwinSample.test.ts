// src/perflab/screens/twin/guestTwinSample.test.ts
//
// B2 (D6): the guest twin preview shows a SAMPLE athlete, and nothing else. Its VO₂ / Profile
// tiles used to read the cached /compute-metrics result (`state.fieldTest`) on "today", which
// put a real — and miscalculated, male-graded — number among sample values under a "field test"
// label. The preview must read no cached result, and both tiles must say they are sample data.
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { p } from "@/testing/importGraph";

const source = readFileSync(p("perflab", "screens", "twin", "GuestTwinPreview.tsx"), "utf8");
const code = source
  .split("\n")
  .filter((line) => !line.trim().startsWith("//"))
  .join("\n");

it("reads no cached field-test result", () => {
  expect(code).not.toMatch(/fieldTest|ftDone|vo2_max|fatigue_percent|fatigue_profile/);
});

it("labels the VO₂max and Profile tiles as sample data", () => {
  for (const label of ["VO₂max", "Profile"]) {
    const tile = code.match(new RegExp(`<MiniTile[^>]*label="${label}"[^>]*/>`));
    expect(tile, label).not.toBeNull();
    expect(tile![0]).toContain('foot="sample data"');
    expect(tile![0]).toContain("sample data.");
  }
});

it("still says the whole preview is a sample athlete", () => {
  expect(code).toContain("Preview — sample athlete.");
});
