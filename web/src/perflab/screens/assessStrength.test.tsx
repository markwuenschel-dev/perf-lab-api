// @vitest-environment jsdom
//
// src/perflab/screens/assessStrength.test.tsx
//
// Assess: a canonical-lift card takes a characterized strength report in the athlete's
// selected unit and sends kilograms to POST /v1/benchmarks/strength-evidence.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { lbsToKg } from "@/lib/units";
import type { AssessmentBenchmarkCard, AssessmentSurfaceRead, StrengthOutcome } from "@/types";
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

/** What POST /v1/benchmarks/strength-evidence returns for a recorded report (StrengthOutcome). */
const RECORDED: StrengthOutcome = {
  outcome: "recorded",
  reason: null,
  value_available: true,
  observation: {
    id: 41, user_id: 7, benchmark_definition_id: 3, benchmark_code: "pl_e1rm_squat",
    observed_at: "2026-10-12T10:00:00", raw_value: 142.9, secondary_value: null,
    normalized_value: null, validity_status: "valid", source: "manual",
    state_disposition: "applied", state_disposition_reason: null,
  },
  report: null,
};

beforeEach(() => {
  submitStrengthEvidence.mockReset();
  submitStrengthEvidence.mockResolvedValue(RECORDED);
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
        idempotency_key: expect.any(String),
      },
      TOKEN,
    );
  });
});

const failure = { message: "Network down.", status: 0 };

function saveSet(load: string, reps = "5") {
  fireEvent.change(screen.getByLabelText("Load in lb"), { target: { value: load } });
  fireEvent.change(screen.getByLabelText("Reps"), { target: { value: reps } });
  fireEvent.click(screen.getByRole("button", { name: "Save result" }));
}

async function openSetForm() {
  fireEvent.click(await screen.findByRole("button", { name: "Log result" }));
  fireEvent.click(screen.getByRole("radio", { name: "A set I did" }));
}

const keyOf = (call: number) => submitStrengthEvidence.mock.calls[call][0].idempotency_key as string;
const UNCONFIRMED = /wasn't confirmed, so it may already be recorded/;

describe("one key per logical submission (P4-3)", () => {
  it("keeps an unanswered save for Retry, which resends the same body and key", async () => {
    submitStrengthEvidence.mockRejectedValueOnce(failure).mockRejectedValueOnce(failure);
    render(<AssessmentSurfaceScreen />);
    await openSetForm();

    saveSet("225");
    await screen.findByText(UNCONFIRMED);
    // Save is not offered while the attempt is unresolved: the athlete retries or discards.
    expect(screen.queryByRole("button", { name: "Save result" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(2));
    expect(submitStrengthEvidence.mock.calls[1][0]).toEqual(submitStrengthEvidence.mock.calls[0][0]);

    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(3));
    expect(keyOf(2)).toBe(keyOf(0));
    await waitFor(() => expect(screen.queryByText(UNCONFIRMED)).toBeNull());
  });

  it("keeps the original attempt when its Retry is refused, and a later Retry resends the original key", async () => {
    submitStrengthEvidence
      .mockRejectedValueOnce(failure) // committed, reply lost
      .mockRejectedValueOnce({ message: "Session expired.", status: 401 });
    render(<AssessmentSurfaceScreen />);
    await openSetForm();

    saveSet("225");
    await screen.findByText(UNCONFIRMED);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Session expired.");
    screen.getByText(UNCONFIRMED);
    expect(screen.queryByRole("button", { name: "Save result" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(3));
    expect([keyOf(1), keyOf(2)]).toEqual([keyOf(0), keyOf(0)]);
    await waitFor(() => expect(screen.queryByText(UNCONFIRMED)).toBeNull());
  });

  it("keeps the unresolved attempt on the card after Do this later, and Retry still resends it", async () => {
    submitStrengthEvidence.mockRejectedValueOnce(failure);
    render(<AssessmentSurfaceScreen />);
    await openSetForm();
    saveSet("225");
    await screen.findByText(UNCONFIRMED);

    fireEvent.click(screen.getByRole("button", { name: "Do this later" }));
    screen.getByText(UNCONFIRMED);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(2));
    expect(submitStrengthEvidence.mock.calls[1][0]).toEqual(submitStrengthEvidence.mock.calls[0][0]);
  });

  it("Discard then Save records the same set again under a new key", async () => {
    submitStrengthEvidence.mockRejectedValueOnce(failure);
    render(<AssessmentSurfaceScreen />);
    await openSetForm();
    saveSet("225");
    await screen.findByText(UNCONFIRMED);

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    saveSet("225");
    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(2));
    const { idempotency_key: first, ...firstFacts } = submitStrengthEvidence.mock.calls[0][0];
    const { idempotency_key: second, ...secondFacts } = submitStrengthEvidence.mock.calls[1][0];
    expect(secondFacts).toEqual(firstFacts);
    expect(second).not.toBe(first);
  });

  it("drops the attempt on a refusal, so the next Save is a new key", async () => {
    submitStrengthEvidence.mockRejectedValueOnce({ message: "Report refused.", status: 422 });
    render(<AssessmentSurfaceScreen />);
    await openSetForm();
    saveSet("225");
    await screen.findByText("Report refused.");
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();

    saveSet("225");
    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(2));
    expect(keyOf(1)).not.toBe(keyOf(0));
  });

  it("mints a new key for the same set recorded again after a success", async () => {
    render(<AssessmentSurfaceScreen />);
    await openSetForm();
    saveSet("225");
    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(1));

    await openSetForm();
    saveSet("225");
    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(2));
    const { idempotency_key: first, ...firstFacts } = submitStrengthEvidence.mock.calls[0][0];
    const { idempotency_key: second, ...secondFacts } = submitStrengthEvidence.mock.calls[1][0];
    expect(secondFacts).toEqual(firstFacts);
    expect(second).not.toBe(first);
  });
});

describe("a set kept without an estimate (P4-3)", () => {
  it("closes the form and says on the card what was recorded", async () => {
    const reportOnly: StrengthOutcome = {
      outcome: "report_only",
      reason: "effort_unknown",
      value_available: false,
      observation: null,
      report: {
        id: 7, benchmark_code: "pl_e1rm_squat", method: "rep_set", collection_mode: "onboarding_onramp",
        performed_at: null, load_kg: lbsToKg(225), reps: 5, rpe: null, rir: null,
        reason: "effort_unknown", created_at: "2026-10-12T10:00:00", superseded_by_observation_id: null,
        superseded_at: null,
      },
    };
    submitStrengthEvidence.mockResolvedValue(reportOnly);
    render(<AssessmentSurfaceScreen />);
    await openSetForm();
    saveSet("225");

    const notice = await screen.findByRole("status");
    expect(notice.textContent).toBe("225 lb × 5 recorded; 1RM estimate unavailable because effort is unknown.");
    expect(screen.queryByLabelText("Load in lb")).toBeNull();
  });
});
