// src/perflab/overlays/prescriptionPrefill.test.ts
//
// The recommended workout pre-fills Log Workout as TARGETS. These pin the honesty half:
// an untouched recommendation is not a logged set, "Done as prescribed" copies only what
// the target states unambiguously, the RPE cap never becomes a reported RPE, and the
// planned-session link is claimed only when a prescribed exercise was actually recorded.
import { describe, expect, it } from "vitest";
import type { ExerciseCatalogOut, ExercisePrescription, PlannedSessionRead } from "@/types";
import {
  exercisesFromStoredPrescription,
  isoLocalDate,
  pickTodaysPendingSession,
  plannedGroup,
} from "./prescriptionPrefill";
import {
  confirmAsPrescribed,
  deriveModality,
  describeTarget,
  groupsToSets,
  topSetKeys,
} from "./setBuilderLogic";
import { buildWorkoutLog, NO_WELLNESS_REPORTED } from "./workoutLogBody";

const SQUAT: ExercisePrescription = {
  name: "Back Squat",
  sets: 5,
  reps: "3",
  prescribed_load_kg: 120,
  rpe_cap: 8,
  load_note: "86% of e1RM 140 kg",
};
const RUN: ExercisePrescription = {
  name: "Easy Run",
  sets: 1,
  reps: "30-40 min conversational pace",
  load_note: "Autoregulate by RPE",
};
const catalog = (over: Partial<ExerciseCatalogOut> = {}): ExerciseCatalogOut =>
  ({ id: 1, name: "Back Squat", load_type: "barbell", modality: "Strength", ...over }) as ExerciseCatalogOut;

describe("a pre-filled group is a target, not a reading", () => {
  it("carries the prescription as its target and sets no reading", () => {
    const g = plannedGroup(SQUAT, catalog(), 1);
    expect(g.target).toEqual({ sets: 5, reps: "3", loadKg: 120, rpeCap: 8, note: "86% of e1RM 140 kg" });
    expect(g.reps).toBeUndefined();
    expect(g.loadKg).toBeUndefined();
    expect(g.rpe).toBeUndefined();
    expect(g.confirmed).toBe(false);
  });

  it("until confirmed it is not logged, not a top set, and does not decide the modality", () => {
    const g = plannedGroup(SQUAT, catalog(), 1);
    expect(groupsToSets([g])).toEqual([]);
    expect(deriveModality([g])).toBeNull();
    expect(topSetKeys([{ ...g, loadKg: 120 }]).size).toBe(0);
  });

  it("DONE AS PRESCRIBED copies sets, whole reps and kg — never the RPE cap", () => {
    const done = confirmAsPrescribed(plannedGroup(SQUAT, catalog(), 1));
    expect(groupsToSets([done])).toEqual([
      expect.objectContaining({ sets: 5, reps: 3, load_kg: 120, rpe: null }),
    ]);
    expect(deriveModality([done])).toBe("Strength");
  });

  it("leaves a rep range or a pace string for the athlete to enter", () => {
    const run = confirmAsPrescribed(
      plannedGroup(RUN, catalog({ name: "Easy Run", load_type: "time", modality: "Running" }), 2),
    );
    expect(run.reps).toBeUndefined();
    const range = confirmAsPrescribed(plannedGroup({ ...SQUAT, reps: "3-5" }, catalog(), 3));
    expect(range.reps).toBeUndefined();
    expect(range.loadKg).toBe(120);
  });

  it("never copies kg onto a movement that takes no external load", () => {
    const bw = confirmAsPrescribed(
      plannedGroup({ ...SQUAT, name: "Push-up" }, catalog({ name: "Push-up", load_type: "bodyweight" }), 4),
    );
    expect(bw.loadKg).toBeUndefined();
  });

  it("describes the target the way the athlete reads it", () => {
    expect(describeTarget(plannedGroup(SQUAT, null, 1).target!)).toBe("5 × 3 @ 120 kg · RPE ≤ 8");
    expect(describeTarget(plannedGroup(RUN, null, 1).target!)).toBe("1 × 30-40 min conversational pace");
  });
});

describe("which recommendation pre-fills", () => {
  const session = (over: Partial<PlannedSessionRead>) =>
    ({ id: 1, scheduled_date: "2026-09-13", status: "pending", prescribed_content: null, ...over }) as PlannedSessionRead;

  it("today's pending session, lowest id first", () => {
    const picked = pickTodaysPendingSession(
      [
        session({ id: 9 }),
        session({ id: 4 }),
        session({ id: 2, status: "completed" }),
        session({ id: 1, scheduled_date: "2026-09-14" }),
      ],
      "2026-09-13",
    );
    expect(picked?.id).toBe(4);
  });

  it("none when nothing is pending today", () => {
    expect(pickTodaysPendingSession([session({ status: "skipped" })], "2026-09-13")).toBeNull();
  });

  it("reads a stored prescription's exercises without trusting its shape", () => {
    expect(exercisesFromStoredPrescription(null)).toEqual([]);
    expect(exercisesFromStoredPrescription({ exercises: "nope" })).toEqual([]);
    expect(exercisesFromStoredPrescription({ exercises: [SQUAT, { sets: 3 }, null] })).toEqual([SQUAT]);
  });

  it("dates the query by the local calendar day", () => {
    expect(isoLocalDate(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
  });
});

describe("the planned-session link", () => {
  const log = (groups: ReturnType<typeof plannedGroup>[], plannedSessionId: number | null) =>
    buildWorkoutLog("strength", 7, 45, null, NO_WELLNESS_REPORTED, groups, plannedSessionId);

  it("is omitted when no prescribed exercise was recorded", () => {
    const body = log([plannedGroup(SQUAT, catalog(), 1)], 41);
    expect(body).not.toBeNull();
    expect(body).not.toHaveProperty("planned_session_id");
  });

  it("is sent once the athlete confirms a prescribed exercise", () => {
    expect(log([confirmAsPrescribed(plannedGroup(SQUAT, catalog(), 1))], 41)?.planned_session_id).toBe(41);
  });

  it("is omitted when the recommendation did not come from a planned session", () => {
    expect(log([confirmAsPrescribed(plannedGroup(SQUAT, catalog(), 1))], null)).not.toHaveProperty(
      "planned_session_id",
    );
  });
});
