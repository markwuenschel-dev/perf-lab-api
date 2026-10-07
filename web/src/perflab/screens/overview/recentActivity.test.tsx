// @vitest-environment jsdom
//
// P3a: a workout saved without a training-state update says so on its Recent activity row;
// an applied workout, or one logged before dispositions existed, carries no label.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { WorkoutLogSummary } from "@/types";
import { RecentActivity } from "./AuthedOverview";

const workout = (over: Partial<WorkoutLogSummary>): WorkoutLogSummary => ({
  id: 1, logged_at: "2026-10-06T09:00:00", session_timestamp: "2026-10-06T08:00:00", modality: "running",
  duration_minutes: 40, session_rpe: 6, distance_meters: 8000, total_volume_load: 0, is_benchmark: false,
  ...over,
});

afterEach(cleanup);

describe("Recent activity (P3a)", () => {
  it("labels only the record-only workout", () => {
    render(
      <RecentActivity
        resource={{
          status: "success",
          refresh: { status: "idle" },
          data: [
            workout({ id: 1, state_disposition: "record_only", state_disposition_reason: "event_before_current_state" }),
            workout({ id: 2, state_disposition: "applied" }),
            workout({ id: 3 }),
          ],
        }}
      />,
    );
    expect(screen.getAllByText(/training state not updated/)).toHaveLength(1);
  });
});
