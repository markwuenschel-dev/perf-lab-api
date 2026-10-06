import { describe, expect, it } from "vitest";
import { reopenFloorIso } from "./sessionActions";

describe("the earliest day a missed session may be reopened onto", () => {
  it("is the UTC date when the local calendar still shows yesterday (review repro)", () => {
    // 00:30Z on 7 Oct: New York still reads 6 Oct, the UTC server reads 7 Oct.
    expect(reopenFloorIso("2026-10-06", new Date("2026-10-07T00:30:00Z"))).toBe("2026-10-07");
  });

  it("is the local date when the local calendar is already ahead of UTC", () => {
    // 23:30Z on 6 Oct: Tokyo already reads 7 Oct; anything the server allows is later still.
    expect(reopenFloorIso("2026-10-07", new Date("2026-10-06T23:30:00Z"))).toBe("2026-10-07");
  });

  it("is that date when both agree", () => {
    expect(reopenFloorIso("2026-10-06", new Date("2026-10-06T12:00:00Z"))).toBe("2026-10-06");
  });
});
