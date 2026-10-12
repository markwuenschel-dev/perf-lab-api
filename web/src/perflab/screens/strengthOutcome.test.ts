import { describe, expect, it } from "vitest";
import type { StrengthOutcome } from "@/types";
import { outcomeAcknowledgement } from "./strengthOutcome";

function recorded(outcome: StrengthOutcome["outcome"] = "recorded"): StrengthOutcome {
  return {
    outcome,
    reason: null,
    value_available: true,
    observation: {
      id: 1, user_id: 7, benchmark_definition_id: 3, benchmark_code: "pl_e1rm_squat",
      observed_at: "2026-10-12T10:00:00", raw_value: 116.7, secondary_value: null,
      normalized_value: null, validity_status: "valid", source: "manual",
      state_disposition: "applied", state_disposition_reason: null,
    },
    report: null,
  };
}

describe("outcomeAcknowledgement", () => {
  it("calls a tested max a 1RM and a set's or an estimate's value an estimate", () => {
    expect(outcomeAcknowledgement(recorded(), "kg", "tested_max")).toBe("Saved. 1RM 116.7 kg recorded.");
    expect(outcomeAcknowledgement(recorded(), "kg", "rep_set")).toBe("Saved. Estimated 1RM 116.7 kg recorded.");
    expect(outcomeAcknowledgement(recorded(), "kg", "estimate")).toBe("Saved. Estimated 1RM 116.7 kg recorded.");
  });

  it("says an already-recorded evidence row was recorded earlier, with its stored value", () => {
    expect(outcomeAcknowledgement(recorded("already_recorded"), "kg", "rep_set")).toBe(
      "Already recorded earlier. Estimated 1RM 116.7 kg recorded.",
    );
  });
});
