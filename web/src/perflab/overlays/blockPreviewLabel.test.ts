import { describe, expect, it } from "vitest";
import { previewDayLabel } from "./blockPreviewLabel";

const slot = { day_of_week: 3, category: "Strength — Volume", modality: "Strength" };

describe("previewDayLabel", () => {
  it("names the weekday the server resolved", () => {
    expect(previewDayLabel({ ...slot, resolved_weekday: 6 })).toBe("Saturday");
    expect(previewDayLabel({ ...slot, resolved_weekday: 1 })).toBe("Monday");
    expect(previewDayLabel({ ...slot, resolved_weekday: 7 })).toBe("Sunday");
  });

  it.each([
    ["absent (a server from before P5b)", undefined],
    ["null", null],
    ["out of range", 0],
    ["out of range", 8],
    ["fractional", 2.5],
  ])("reads the block-relative day, never a weekday, when the label is %s", (_why, resolved) => {
    expect(previewDayLabel({ ...slot, resolved_weekday: resolved })).toBe("Day 3");
  });

  it("never turns the block-relative day into a weekday", () => {
    expect(previewDayLabel({ ...slot, day_of_week: 1 })).toBe("Day 1");
  });
});
