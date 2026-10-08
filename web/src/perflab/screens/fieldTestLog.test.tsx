// @vitest-environment jsdom
//
// P3-pre: a field test dated before the model's latest update is listed, labelled as not having
// updated the training state; an applied one, or one from before dispositions existed, is not.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { BenchmarkObservationRead } from "@/types";
import { FieldTestLogCard } from "./HistoryScreen";

const obs = (over: Partial<BenchmarkObservationRead>): BenchmarkObservationRead => ({
  id: 1, user_id: 1, benchmark_definition_id: 1, benchmark_code: "vo2max_field", observed_at: "2026-10-01T09:00:00",
  raw_value: 48.2, secondary_value: null, normalized_value: 60, validity_status: "valid", source: "benchmark_test",
  ...over,
});

afterEach(cleanup);

describe("Field test log (P3-pre)", () => {
  it("labels only the record-only observation", () => {
    render(
      <FieldTestLogCard
        resource={{
          status: "success",
          refresh: { status: "idle" },
          data: [
            obs({ id: 1, state_disposition: "record_only", state_disposition_reason: "event_before_current_state" }),
            obs({ id: 2, state_disposition: "applied" }),
            obs({ id: 3 }),
          ],
        }}
      />,
    );
    expect(screen.getAllByText(/training state not updated/)).toHaveLength(1);
  });
});
