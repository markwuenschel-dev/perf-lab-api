// @vitest-environment jsdom
//
// src/perflab/screens/assessInfo.test.tsx
//
// Every Assess card explains what it measures and, separately, how to measure it — and says
// plainly when no measurement protocol is established. Essential entry requirements stay
// visible in the form rather than only inside help.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { AssessmentBenchmarkCard, AssessmentSurfaceRead } from "@/types";
import { AssessmentSurfaceScreen } from "./AssessmentSurfaceScreen";

const BASE: Pick<
  AssessmentBenchmarkCard,
  | "domain_lenses"
  | "domain_lenses_source"
  | "eligible"
  | "last_observed_at"
  | "recommend_rank"
  | "recommended"
  | "utility"
  | "utility_model_version"
> = {
  domain_lenses: [],
  domain_lenses_source: "catalog",
  eligible: true,
  last_observed_at: null,
  recommend_rank: null,
  recommended: false,
  utility: 0,
  utility_model_version: "test",
};

const VO2: AssessmentBenchmarkCard = {
  ...BASE,
  code: "run_vo2_field_test_300m_1p5mi",
  confidence_status: "provisional",
  description: "An estimate of your VO₂max (aerobic capacity). The onramp aerobic benchmark.",
  domain: "running",
  measures_axes: ["aerobic"],
  metric_type: "score",
  name: "VO₂ field test (300 m + 1.5 mi)",
  protocol_summary: "A two-part run test: a 300 m all-out run and a 1.5 mi time trial.",
  strength_evidence_entry: false,
  unit: "ml_kg_min",
};

const REPEATABILITY: AssessmentBenchmarkCard = {
  ...BASE,
  code: "mm_repeatability_test",
  confidence_status: null,
  description: "How well you can repeat a workout effort, as a score.",
  domain: "mixed",
  measures_axes: ["work_capacity"],
  metric_type: "score",
  name: "Repeatability / repeat WOD test",
  protocol_summary: "Measurement protocol not yet defined.",
  strength_evidence_entry: false,
  unit: "score",
};

const SQUAT: AssessmentBenchmarkCard = {
  ...BASE,
  code: "pl_e1rm_squat",
  confidence_status: null,
  description: "Your estimated one-rep max (e1RM) for the squat.",
  domain: "powerlifting",
  measures_axes: ["max_strength"],
  metric_type: "load",
  name: "Squat e1RM",
  protocol_summary: "Report a tested 1-rep max, a set you did, or your own estimate.",
  strength_evidence_entry: true,
  unit: "kg",
};

const SURFACE: AssessmentSurfaceRead = {
  active_domains: [],
  groups: [
    { domain: "running", cards: [VO2] },
    { domain: "mixed", cards: [REPEATABILITY] },
    { domain: "powerlifting", cards: [SQUAT] },
  ],
  mode: "onramp",
  policy_version: "test",
  recommended: [],
};

vi.mock("@/api/perfLabClient", () => ({
  getAssessmentSurface: () => Promise.resolve(SURFACE),
  getOnboardingState: () => Promise.reject(new Error("not under test")),
  getNextSession: () => Promise.reject(new Error("not under test")),
  completeOnboarding: vi.fn(),
  submitBenchmarkObservation: vi.fn(),
  submitStrengthEvidence: vi.fn(),
}));

vi.mock("@/auth/useAuth", () => ({ useAuth: () => ({ token: "athlete-token" }) }));

vi.mock("../store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../store")>();
  return {
    ...actual,
    usePerfLab: () => ({
      state: { settings: { units: "Metric (km)", goal: "Strength" } },
      actions: { openAuth: vi.fn() },
    }),
  };
});

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

afterEach(cleanup);

async function openHelp(name: string) {
  fireEvent.click(await screen.findByRole("button", { name }));
  return screen.getByRole("dialog");
}

describe("Assess card help", () => {
  it("separates what a benchmark measures from how to measure it", async () => {
    render(<AssessmentSurfaceScreen />);
    const dialog = await openHelp(`About ${VO2.name}`);
    expect(within(dialog).getByText("What it measures")).toBeTruthy();
    expect(within(dialog).getByText(VO2.description as string)).toBeTruthy();
    expect(within(dialog).getByText("How to measure")).toBeTruthy();
    expect(within(dialog).getByText(VO2.protocol_summary as string)).toBeTruthy();
    expect(within(dialog).getByText("Aerobic")).toBeTruthy();
  });

  it("no longer prints the protocol on the card itself", async () => {
    render(<AssessmentSurfaceScreen />);
    await screen.findByRole("button", { name: `About ${VO2.name}` });
    expect(screen.queryByText(VO2.protocol_summary as string)).toBeNull();
  });

  it("says plainly when no measurement protocol is established", async () => {
    render(<AssessmentSurfaceScreen />);
    const dialog = await openHelp(`About ${REPEATABILITY.name}`);
    expect(within(dialog).getByText("Measurement protocol not yet defined.")).toBeTruthy();
  });

  it("shows units as units", async () => {
    render(<AssessmentSurfaceScreen />);
    expect(await screen.findByText("ml/kg/min")).toBeTruthy();
    expect(screen.queryByText("ml_kg_min")).toBeNull();
  });

  it("explains the confidence label", async () => {
    render(<AssessmentSurfaceScreen />);
    const dialog = await openHelp("About this confidence label");
    expect(within(dialog).getByText("provisional")).toBeTruthy();
  });

  it("keeps a strength report's entry requirements visible, with help beside them", async () => {
    render(<AssessmentSurfaceScreen />);
    const logButtons = await screen.findAllByRole("button", { name: "Log result" });
    fireEvent.click(logButtons[logButtons.length - 1]); // the squat card
    fireEvent.click(screen.getByRole("radio", { name: "A set I did" }));

    expect(screen.getByText("Effort (RPE) is 1 to 10, optional")).toBeTruthy();
    expect(screen.getByPlaceholderText("Load (kg)")).toBeTruthy();
    expect(screen.getByText("Date performed")).toBeTruthy();
    for (const name of ["About these options", "About RPE", "About the date"]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
  });
});
