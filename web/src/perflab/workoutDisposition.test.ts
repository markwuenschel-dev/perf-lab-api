import { describe, expect, it } from "vitest";
import { dispositionNotice, dispositionTag } from "./workoutDisposition";

describe("what a logged workout did to the training state (P3a)", () => {
  it("an applied workout, or one logged before dispositions existed, says nothing", () => {
    for (const d of ["applied", null, undefined] as const) {
      expect(dispositionNotice(d, null)).toBeNull();
      expect(dispositionTag(d)).toBeNull();
    }
  });

  it.each([
    ["event_before_current_state", /it happened before your model's latest update/],
    ["current_state_in_future", /stamped later than now/],
  ])("record-only (%s) says the update was omitted, and why", (reason, why) => {
    const notice = dispositionNotice("record_only", reason)!;
    expect(notice).toMatch(/^Recorded\. Training-state update omitted: /);
    expect(notice).toMatch(why);
    // Evidence from it still informs prescribed loads: never "it counts for nothing".
    expect(notice).not.toMatch(/nothing|ignored|discarded/i);
    expect(dispositionTag("record_only")).toBe("training state not updated");
  });
});
