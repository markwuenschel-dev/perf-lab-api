// src/perflab/screens/strengthEvidenceBody.test.ts
//
// A strength report states what happened and nothing more: no authority flags, no computed
// e1RM, unknown values omitted, weights converted from the athlete's unit to kilograms at the
// request boundary, and a chosen day sent as that day's local midnight so "today" can never be
// a future performance. Onboarding additionally requires a date on a tested max or a set.
import { describe, expect, it } from "vitest";
import { lbsToKg } from "@/lib/units";
import {
  EMPTY_STRENGTH_FORM,
  isBlankStrengthForm,
  strengthEvidenceBody,
  strengthReport,
  type StrengthForm,
} from "./strengthEvidenceBody";

const NOW = new Date(2026, 8, 14, 9, 30); // 14 Sep 2026, 09:30 local
const form = (over: Partial<StrengthForm>): StrengthForm => ({ ...EMPTY_STRENGTH_FORM, ...over });

/** Keys the server derives and a client must never send. */
const AUTHORITY_KEYS = [
  "raw_value", "value_semantics", "evidence_type", "affects_prescription",
  "affects_capacity", "source", "source_type", "validity_status", "formula",
  "effort_fidelity", "observed_at", "observation_model",
];

function body(over: Partial<StrengthForm>, mode: "onramp" | "retest" = "retest") {
  const r = strengthEvidenceBody("pl_e1rm_squat", mode, form(over), { now: NOW });
  if (!r.ok) throw new Error(`expected a body, got: ${r.error}`);
  return r.body as Record<string, unknown>;
}

describe("what each method sends", () => {
  it("a tested max sends the weight and the method, nothing derived", () => {
    expect(body({ method: "tested_max", weight: "140" })).toEqual({
      benchmark_code: "pl_e1rm_squat",
      method: "tested_max",
      collection_mode: "retest",
      value_kg: 140,
    });
  });

  it("a set sends load and reps, and effort only when entered", () => {
    expect(body({ method: "rep_set", load: "120", reps: "3", rpe: "9" })).toMatchObject({
      method: "rep_set", load_kg: 120, reps: 3, rpe: 9,
    });
    const noEffort = body({ method: "rep_set", load: "120", reps: "3" });
    expect(noEffort).not.toHaveProperty("rpe");
    expect(noEffort).not.toHaveProperty("value_kg");
  });

  it("an estimate sends the weight and the method", () => {
    expect(body({ method: "estimate", weight: "150" })).toMatchObject({ method: "estimate", value_kg: 150 });
  });

  it("maps the Assess mode to its collection mode", () => {
    expect(body({ weight: "140" }, "onramp").collection_mode).toBe("onboarding_onramp");
  });

  it.each([
    { method: "tested_max" as const, weight: "140" },
    { method: "rep_set" as const, load: "120", reps: "3", rpe: "9" },
    { method: "estimate" as const, weight: "150" },
  ])("never sends a key the server derives ($method)", (over) => {
    const b = body({ ...over, performedOn: "2026-09-10" });
    for (const key of AUTHORITY_KEYS) expect(b, key).not.toHaveProperty(key);
  });
});

describe("units", () => {
  it("converts pounds to kilograms at the request boundary — for a weight and a set's load", () => {
    const tested = strengthEvidenceBody("pl_e1rm_squat", "retest", form({ weight: "315" }), { now: NOW, unit: "lb" });
    expect(tested.ok && tested.body.value_kg).toBe(lbsToKg(315));
    const set = strengthReport("pl_e1rm_squat", form({ method: "rep_set", load: "225", reps: "3", rpe: "9" }),
      { now: NOW, unit: "lb" });
    expect(set.ok && set.report.load_kg).toBe(lbsToKg(225));
  });

  it("names the selected unit when a weight is missing", () => {
    expect(strengthReport("pl_e1rm_squat", form({}), { now: NOW, unit: "lb" }))
      .toEqual({ ok: false, error: "Enter the weight in lb." });
    expect(strengthReport("pl_e1rm_squat", form({ method: "rep_set", reps: "3" }), { now: NOW, unit: "lb" }))
      .toEqual({ ok: false, error: "Enter the load you lifted, in lb." });
  });

  it("sends kilograms unchanged when the unit is kilograms", () => {
    expect(body({ weight: "140" }).value_kg).toBe(140);
  });
});

describe("the performance date", () => {
  it("is omitted when not entered — unknown, not today", () => {
    expect(body({ weight: "140" })).not.toHaveProperty("performed_at");
  });

  it("is the chosen day's local midnight, as an instant", () => {
    expect(body({ weight: "140", performedOn: "2026-09-10" }).performed_at).toBe(
      new Date(2026, 8, 10).toISOString(),
    );
  });

  it("accepts today, which is never in the future at local midnight", () => {
    expect(body({ weight: "140", performedOn: "2026-09-14" }).performed_at).toBe(
      new Date(2026, 8, 14).toISOString(),
    );
  });

  it("refuses a future day and an impossible one", () => {
    expect(strengthEvidenceBody("pl_e1rm_squat", "retest", form({ weight: "140", performedOn: "2026-09-15" }), { now: NOW }))
      .toEqual({ ok: false, error: "That date is in the future." });
    expect(strengthEvidenceBody("pl_e1rm_squat", "retest", form({ weight: "140", performedOn: "2026-02-31" }), { now: NOW }))
      .toEqual({ ok: false, error: "Enter a real date." });
  });
});

describe("values that are not values", () => {
  it.each(["", "0", "-5", "abc", "Infinity"])("refuses %j as a weight", (weight) => {
    expect(strengthEvidenceBody("pl_e1rm_squat", "retest", form({ weight }), { now: NOW }).ok).toBe(false);
  });

  it.each(["", "0", "2.5", "x"])("refuses %j as reps", (reps) => {
    expect(strengthEvidenceBody("pl_e1rm_squat", "retest", form({ method: "rep_set", load: "100", reps }), { now: NOW }).ok)
      .toBe(false);
  });

  it.each(["0", "11", "nope"])("refuses %j as effort", (rpe) => {
    expect(strengthEvidenceBody("pl_e1rm_squat", "retest",
      form({ method: "rep_set", load: "100", reps: "3", rpe }), { now: NOW }).ok).toBe(false);
  });
});

describe("a report for onboarding", () => {
  const onboarding = { now: NOW, requireDate: true };

  it("needs a date for a tested max or a set", () => {
    expect(strengthReport("pl_e1rm_squat", form({ weight: "140" }), onboarding))
      .toEqual({ ok: false, error: "Add the date you did it." });
    expect(strengthReport("pl_e1rm_squat", form({ method: "rep_set", load: "120", reps: "3" }), onboarding).ok)
      .toBe(false);
  });

  it("does not need a date for an estimate", () => {
    expect(strengthReport("pl_e1rm_squat", form({ method: "estimate", weight: "150" }), onboarding))
      .toEqual({ ok: true, report: { benchmark_code: "pl_e1rm_squat", method: "estimate", value_kg: 150 } });
  });

  it("carries no collection mode — the onboarding route supplies it", () => {
    const r = strengthReport("pl_e1rm_squat", form({ weight: "140", performedOn: "2026-09-10" }), onboarding);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.report).not.toHaveProperty("collection_mode");
  });

  it("treats a lift with nothing entered as blank, whatever method is selected", () => {
    expect(isBlankStrengthForm(form({ method: "rep_set" }))).toBe(true);
    expect(isBlankStrengthForm(form({ performedOn: "2026-09-10" }))).toBe(false);
  });
});
