// src/perflab/overlays/blockPreviewLabel.ts
//
// The day label of a previewed slot in the block-create screen (P5b, ADR-0074).
//
// A slot's `day_of_week` is block-relative: day N of each block week, counted from the start
// date. It is not a weekday: on a block that starts on a Thursday, day 1 is every Thursday. The
// server labels each previewed slot with the weekday it falls on (`resolved_weekday`). A server
// from before P5b sends no label; the slot then reads "Day N", never a weekday name.
import type { WeeklyTemplateSlot } from "@/types";

/** A previewed slot as any server version may send it: the label is absent before P5b. */
export type PreviewSlot = WeeklyTemplateSlot & { resolved_weekday?: number | null };

const WEEKDAYS: Record<number, string> = {
  1: "Monday",
  2: "Tuesday",
  3: "Wednesday",
  4: "Thursday",
  5: "Friday",
  6: "Saturday",
  7: "Sunday",
};

export function previewDayLabel(slot: PreviewSlot): string {
  const weekday = slot.resolved_weekday;
  if (typeof weekday === "number" && Number.isInteger(weekday) && weekday in WEEKDAYS) {
    return WEEKDAYS[weekday];
  }
  return `Day ${slot.day_of_week}`;
}
