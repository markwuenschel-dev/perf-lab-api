// What the create-block modal sends for a multi-style block.
//
// The modal used to hard-code `modality_mix: {}`, so the backend's multi-style machinery
// (ADR-0030) was unreachable from the UI. These pin the request it builds now.
//
// The invariant, stated once: THE MIX IS KEYED BY CANONICAL DOMAIN, and the main style is
// always in it. Shares are SESSION allocation — the server performs the allocation, and the
// modal shows the server's answer rather than computing a second one (see previewPlanningBlock).
import { describe, expect, it } from "vitest";
import { buildBlockCreateRequest, GOAL_DOMAIN, MAIN_SHARE } from "./blockCreateBody";

const base = {
  goal: "Strength" as const,
  secondary: [] as string[],
  intensity: "medium" as const,
  startDate: "2026-09-21",
  durationWeeks: "8",
  sessionsPerWeek: "3",
  targetMinutes: "",
  emphasis: "balanced" as const,
  focus: [] as string[],
};

describe("modality_mix", () => {
  it("stays empty with no secondary style, so the goal's own default week is used", () => {
    expect(buildBlockCreateRequest(base).modality_mix).toEqual({});
  });

  it("gives the main style the larger share and splits the rest evenly", () => {
    const mix = buildBlockCreateRequest({ ...base, secondary: ["running", "conditioning"] })
      .modality_mix as Record<string, number>;

    expect(mix.strength).toBe(MAIN_SHARE);
    expect(mix.running).toBeCloseTo((1 - MAIN_SHARE) / 2, 6);
    expect(mix.conditioning).toBeCloseTo((1 - MAIN_SHARE) / 2, 6);
    expect(Object.values(mix).reduce((a, b) => a + b, 0)).toBeCloseTo(1, 6);
  });

  it("keys the mix by canonical domain, not by the block-goal label", () => {
    const mix = buildBlockCreateRequest({ ...base, goal: "Hyrox", secondary: ["strength"] })
      .modality_mix as Record<string, number>;

    // Hyrox canonicalizes to "mixed" server-side; sending "Hyrox" would be dropped silently.
    expect(Object.keys(mix).sort()).toEqual(["mixed", "strength"]);
    expect(GOAL_DOMAIN.Hyrox).toBe("mixed");
  });

  it("never double-counts the main style when it is also picked as secondary", () => {
    const mix = buildBlockCreateRequest({ ...base, secondary: ["strength"] })
      .modality_mix as Record<string, number>;

    expect(mix).toEqual({});
  });

  it("distinguishes styles that share a modality label", () => {
    const mix = buildBlockCreateRequest({ ...base, secondary: ["powerlifting"] })
      .modality_mix as Record<string, number>;

    // Both render as "Strength" days; only the domain keys tell the server which is which.
    expect(Object.keys(mix).sort()).toEqual(["powerlifting", "strength"]);
  });
});

describe("workload preference", () => {
  it("travels with the request", () => {
    expect(buildBlockCreateRequest({ ...base, intensity: "hard" }).intensity).toBe("hard");
    expect(buildBlockCreateRequest({ ...base, intensity: "easy" }).intensity).toBe("easy");
  });

  it("defaults to medium", () => {
    expect(buildBlockCreateRequest(base).intensity).toBe("medium");
  });
});
