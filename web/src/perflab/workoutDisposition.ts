// P3a: what a logged workout did to the training state, in the athlete's words.
//
// A workout timed before the model's latest update is recorded without updating the
// training state. Everything else about it is kept: the workout, its sets, any
// planned-session link, and strength evidence that can still inform prescribed loads. So the
// wording is "training-state update omitted", never "this workout counts for nothing".

type Disposition = "applied" | "record_only" | null | undefined;

/** The full explanation, shown where the athlete just logged (the log form). */
export function dispositionNotice(disposition: Disposition, reason: string | null | undefined): string | null {
  if (disposition !== "record_only") return null;
  const why =
    reason === "current_state_in_future"
      ? "your model's latest update is stamped later than now, so no workout can be applied after it yet"
      : "it happened before your model's latest update";
  return `Recorded. Training-state update omitted: ${why}. The workout and any strength evidence from it are kept.`;
}

/** The short label on a workout row (Overview, Planning). Applied and older logs say nothing. */
export function dispositionTag(disposition: Disposition): string | null {
  return disposition === "record_only" ? "training state not updated" : null;
}
