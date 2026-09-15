// @vitest-environment jsdom
//
// src/perflab/screens/assessStrength.test.tsx
//
// Assess: a canonical-lift card takes a characterized strength report in the athlete's
// selected unit and sends kilograms to POST /v1/benchmarks/strength-evidence.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { lbsToKg } from "@/lib/units";
import type { AssessmentBenchmarkCard, AssessmentSurfaceRead } from "@/types";
import { AssessmentSurfaceScreen } from "./AssessmentSurfaceScreen";

const TOKEN = "athlete-token";
const submitStrengthEvidence = vi.fn();

const SQUAT_CARD: AssessmentBenchmarkCard = {
  code: "pl_e1rm_squat",
  confidence_status: null,
  description: null,
  domain: "strength",
  domain_lenses: [],
  domain_lenses_source: "catalog",
  eligible: true,
  last_observed_at: null,
  measures_axes: ["max_strength"],
  metric_type: "e1rm",
  name: "Back squat e1RM",
  protocol_summary: null,
  recommend_rank: null,
  recommended: false,
  strength_evidence_entry: true,
  unit: "kg",
  utility: 0,
  utility_model_version: "test",
};

const SURFACE: AssessmentSurfaceRead = {
  active_domains: ["strength"],
  groups: [{ domain: "strength", cards: [SQUAT_CARD] }],
  mode: "onramp",
  policy_version: "test",
  recommended: [],
};

vi.mock("@/api/perfLabClient", () => ({
  getAssessmentSurface: () => Promise.resolve(SURFACE),
  // The twin banner and the measurement ask are not under test; a failed load renders nothing.
  getOnboardingState: () => Promise.reject(new Error("not under test")),
  getNextSession: () => Promise.reject(new Error("not under test")),
  completeOnboarding: vi.fn(),
  submitBenchmarkObservation: vi.fn(),
  submitStrengthEvidence: (...args: unknown[]) => submitStrengthEvidence(...args),
}));

vi.mock("@/auth/useAuth", () => ({ useAuth: () => ({ token: TOKEN }) }));

vi.mock("../store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../store")>();
  return {
    ...actual,
    usePerfLab: () => ({
      state: { settings: { units: "Imperial (mi)", goal: "Strength" } },
      actions: { openAuth: vi.fn() },
    }),
  };
});

beforeEach(() => {
  submitStrengthEvidence.mockReset();
  submitStrengthEvidence.mockResolvedValue({});
});

afterEach(cleanup);

describe("a strength report from Assess", () => {
  it("is typed in pounds for an imperial athlete and sent in kilograms", async () => {
    render(<AssessmentSurfaceScreen />);

    fireEvent.click(await screen.findByRole("button", { name: "Log result" }));
    fireEvent.change(screen.getByLabelText("Weight in lb"), { target: { value: "315" } });
    fireEvent.click(screen.getByRole("button", { name: "Save result" }));

    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(1));
    expect(submitStrengthEvidence).toHaveBeenCalledWith(
      {
        benchmark_code: "pl_e1rm_squat",
        method: "tested_max",
        value_kg: lbsToKg(315),
        collection_mode: "onboarding_onramp",
      },
      TOKEN,
    );
  });
});
