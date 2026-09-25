// The `prescription` field of `/v1/planning/today` is a GENERATED type, and the
// Overview must keep consuming it as one.
//
// History this locks down: openapi.json declared the field as a bare object, so
// `types.gen.ts` emitted `{ [key: string]: unknown } | null`, which is unusable —
// and AuthedOverview.tsx answered that with a hand-written local shape plus two
// `as` casts. A hand-written duplicate of a contract type cannot fail when the
// contract moves; it just goes quietly wrong. The backend now declares
// `WorkoutPrescription | None`, and the ratchets at the bottom of this file keep it
// that way — see the note there on which ratchet proves which claim.
//
// The compile-time half of this guarantee is enforced by `pnpm run check:types`
// (`tsc --noEmit`, tsconfig.json: strict + noUnusedLocals): the fixture below is
// annotated `TodaySessionResponse`, so it only compiles while `prescription` is
// structurally the generated `WorkoutPrescription`. What is asserted at RUNTIME
// here is the generated artifact and the source, which tsc cannot speak about.
import { describe, expect, it } from "vitest";
import type { TodaySessionResponse, WorkoutPrescription } from "@/types";

const GENERATED_REF = 'components["schemas"]["WorkoutPrescription"]';

/** A fully populated response, typed as the contract. See the header note: the
 *  annotation is the compile-time assertion; tsc rejects this file if the field
 *  ever regresses to an index signature. */
const populated: TodaySessionResponse = {
  session: null,
  prescription: {
    type: "Strength",
    focus: "Heavy Lower",
    rationale: "Readiness is high and the block is in an accumulation week.",
    duration_min: 62,
    // Phase 2.2: the server always sends these — they are computed from `structure`, so the
    // generated type marks them required. Null here: this fixture carries no structure.
    calculated_duration_min: null,
    duration_estimate: null,
    model_version: "v0.4",
    exercises: [{ name: "Back Squat", sets: 4, reps: "5", weak_point_tags: [], prescribed_load_kg: 102.5 }],
    why: {
      state_drivers: ["hrv above baseline"],
      goal_alignment: "Strength",
      constraints_applied: ["block:phase=accumulation", "block:rpe_target=8"],
      source_alignment: [],
      warnings: [],
    },
  },
};

describe("TodaySessionResponse.prescription is the generated WorkoutPrescription", () => {
  it("reads model fields through the generated type without a cast", () => {
    // No `as` anywhere in this block — that is the point. Under the old
    // index-signature type every one of these reads was `unknown`.
    const rx: WorkoutPrescription | null | undefined = populated.prescription;
    expect(rx?.focus).toBe("Heavy Lower");
    expect(rx?.duration_min).toBe(62);
    expect(rx?.exercises?.[0]?.name).toBe("Back Squat");
    expect(rx?.why?.constraints_applied).toContain("block:phase=accumulation");
  });

  it("null is still a valid state, so the field stays optional", () => {
    const empty: TodaySessionResponse = { session: null, prescription: null };
    expect(empty.prescription).toBeNull();
  });

  it("types.gen.ts declares the field as a $ref to the model, not an index signature", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const src = readFileSync(join(__dirname, "..", "..", "..", "types.gen.ts"), "utf8");

    const block = /TodaySessionResponse:\s*\{([\s\S]*?)\n {8}\};/.exec(src);
    expect(block, "TodaySessionResponse not found in types.gen.ts").not.toBeNull();
    const body = block![1];

    expect(body).toContain(GENERATED_REF);
    // The exact shape openapi-typescript emitted for the old bare-object declaration.
    expect(body).not.toMatch(/\[key: string\]: unknown/);
  });
});

// ---- Ratchets ---------------------------------------------------------------------
//
// Two DIFFERENT predicates live below, and the difference is the whole point.
//
//   * The identifier ratchet is the one that corresponds to the acceptance criterion
//     ("the hand-declared alias no longer exists anywhere under web/src"). It scans
//     for the identifier itself, in EVERY .ts/.tsx file including test files.
//   * The cast ratchet is a broader style check: no file should assert `.prescription`
//     away from its generated type. It is worth keeping, but it is NOT evidence for
//     the criterion — an alias re-declared and consumed by annotation rather than by
//     an inline cast slips straight past it. `positiveControls` below proves that gap
//     executably rather than asserting it in prose.

/** The retired hand-rolled alias, assembled at RUNTIME from two fragments.
 *
 *  This file scans every file under web/src — itself included, deliberately, because
 *  excluding test files is exactly the hole through which a re-declaration would hide.
 *  A test that searches for an identifier therefore must not contain that identifier
 *  literally, or it convicts itself on every run. Joining the fragments is what keeps
 *  the needle out of this file's own bytes while still producing the exact string.
 *  Do not inline this constant back into a literal. */
const RETIRED_ALIAS = ["Presc", "Dict"].join("");

/** Matches the criterion literally: the identifier appears at all. */
const declaresRetiredAlias = (src: string): boolean => src.includes(RETIRED_ALIAS);

/** The cast fingerprint: a line that reads `.prescription` and also asserts a type. */
const CAST_FINGERPRINT = /^.*\.prescription\b.*\bas\b.*$/m;

/** The form both retired offenders in AuthedOverview.tsx took — an inline cast. */
const RETIRED_CAST_SAMPLE = `const presc = (data ? data.prescription : null) as ${RETIRED_ALIAS} | null;`;

/** The bypass form: same alias, no cast. Annotation instead of assertion. */
const ANNOTATION_BYPASS_SAMPLE = [
  `type ${RETIRED_ALIAS} = { focus?: string };`,
  `const presc: ${RETIRED_ALIAS} | null = data ? data.prescription : null;`,
].join("\n");

async function tsFilesUnderWebSrc(opts: { includeTests: boolean }): Promise<string[]> {
  const { readdirSync } = await import("node:fs");
  const { join } = await import("node:path");
  const root = join(__dirname, "..", "..", "..");
  const walk = (dir: string): string[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
      const full = join(dir, e.name);
      if (e.isDirectory()) return walk(full);
      if (!/\.tsx?$/.test(e.name)) return [];
      if (!opts.includeTests && /\.test\.tsx?$/.test(e.name)) return [];
      return [full];
    });
  return walk(root);
}

describe("the detectors themselves behave as claimed", () => {
  // These are positive controls. They replace a comment that used to claim a result
  // from a run against a previous revision of the tree — a claim nobody could
  // reproduce from the repository as it stands. Each assertion below re-derives that
  // claim from source held right here, every run.
  it("the cast fingerprint matches the retired inline-cast form", () => {
    expect(CAST_FINGERPRINT.test(RETIRED_CAST_SAMPLE)).toBe(true);
  });

  it("the cast fingerprint does NOT match the annotation bypass — so it cannot prove the criterion", () => {
    // This is the gap that made the cast ratchet unusable as evidence for the
    // "identifier is gone" criterion: re-declare the alias, consume it by annotation,
    // and every cast-shaped check stays green.
    expect(CAST_FINGERPRINT.test(ANNOTATION_BYPASS_SAMPLE)).toBe(false);
  });

  it("the identifier detector catches BOTH forms, which is why it is the criterion's proof", () => {
    expect(declaresRetiredAlias(RETIRED_CAST_SAMPLE)).toBe(true);
    expect(declaresRetiredAlias(ANNOTATION_BYPASS_SAMPLE)).toBe(true);
  });

  it("the needle is the real identifier and is absent from this file's own bytes", async () => {
    const { readFileSync } = await import("node:fs");
    expect(RETIRED_ALIAS).toHaveLength(9);
    expect(RETIRED_ALIAS.startsWith("Presc")).toBe(true);
    expect(RETIRED_ALIAS.endsWith("Dict")).toBe(true);
    // If this ever fails, someone inlined the literal and the scan below will report
    // this file forever after. That is the self-conviction trap the join() avoids.
    expect(declaresRetiredAlias(readFileSync(__filename, "utf8"))).toBe(false);
  });
});

describe("the hand-declared prescription alias is gone from web/src", () => {
  it("no file under web/src — test files included — declares or references the identifier", async () => {
    const { readFileSync } = await import("node:fs");
    const files = await tsFilesUnderWebSrc({ includeTests: true });
    // Guard against a walk that silently finds nothing and passes vacuously.
    expect(files.length).toBeGreaterThan(20);

    const offenders = files.filter((f) => declaresRetiredAlias(readFileSync(f, "utf8")));
    expect(
      offenders,
      `the retired hand-rolled prescription alias is declared or referenced in: ${offenders.join(", ")}`,
    ).toEqual([]);
  });
});

describe("no file asserts .prescription away from its generated type", () => {
  it("nothing under web/src writes an inline cast on a .prescription read", async () => {
    const { readFileSync } = await import("node:fs");
    // Test files are excluded HERE only — this module necessarily quotes the cast
    // form in `RETIRED_CAST_SAMPLE` in order to forbid it. The identifier ratchet
    // above needs no such exclusion, which is why it, and not this, is the evidence
    // for the acceptance criterion.
    const files = await tsFilesUnderWebSrc({ includeTests: false });
    expect(files.length).toBeGreaterThan(20);

    const offenders = files.filter((f) => CAST_FINGERPRINT.test(readFileSync(f, "utf8")));
    expect(offenders, `cast .prescription away from the generated type in: ${offenders.join(", ")}`).toEqual([]);
  });
});
