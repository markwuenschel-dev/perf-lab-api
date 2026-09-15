// src/perflab/prescription/LoadExplanation.tsx
//
// Why a prescribed exercise has no suggested weight (S2, decision N1), shown beside the
// exercise it explains and under its effort guidance — never instead of it. Shared by
// Planning and Twin, like WhyThisSession.
//
// The backend decides the category (`ExercisePrescription.load_explanation`, built by
// app/services/prescription_service.py `_explain_loads`); this file only words it. Two rules
// from that decision shape the copy:
//
//   * it explains ELIGIBILITY — never that the athlete got weaker, never an age, and never an
//     instruction to go and test (there is no calibration flow to send anyone to);
//   * "not supported for this exercise" is not "no qualifying evidence": an exercise that no
//     report could ever size gets no link to strength history.
import type { LoadExplanation as LoadExplanationData } from "@/types";
import { CANONICAL_LIFTS } from "../screens/canonicalLifts";

type Reason = NonNullable<LoadExplanationData["reason"]>;

const REVIEW = "Review strength history";
const REPORT = "Report a previous performance";

const NO_EVIDENCE_COPY: Record<Reason, { text: (lift: string) => string; link: string }> = {
  stale: {
    text: (lift) => `No recent qualifying ${lift} evidence. Use the prescribed effort guidance today.`,
    link: REVIEW,
  },
  missing_performance_date: {
    text: () => "We need the performance date before this report can guide a weight.",
    link: REVIEW,
  },
  estimate_not_used: {
    text: () => "Your estimate is saved, but it isn't used to recommend weights.",
    link: REVIEW,
  },
  set_not_qualifying: {
    text: () => "This set is saved, but it doesn't qualify for a weight recommendation.",
    link: REVIEW,
  },
  no_evidence: {
    text: () => "No qualifying strength history yet. Use the prescribed effort guidance.",
    link: REPORT,
  },
  not_qualifying: {
    text: () =>
      "Your saved strength history doesn't qualify for a weight recommendation yet. Use the prescribed effort guidance.",
    link: REVIEW,
  },
};

const NOT_SUPPORTED_TEXT =
  "Weight recommendations aren't available for this exercise. Use the prescribed effort guidance.";

interface LoadExplanationWording {
  text: string;
  /** The label of the link to Assess, or null when strength history cannot help. */
  link: string | null;
}

/** The lift as the athlete calls it: the canonical label for a known code, else the exercise. */
function liftName(benchmarkCode: string | null | undefined, exerciseName: string): string {
  return CANONICAL_LIFTS.find((lift) => lift.code === benchmarkCode)?.label.toLowerCase() ?? exerciseName;
}

/** What to say about an exercise's weight, or null when there is nothing to say. */
function loadExplanationWording(
  explanation: LoadExplanationData | null | undefined,
  exerciseName: string,
): LoadExplanationWording | null {
  if (!explanation) return null;
  switch (explanation.status) {
    case "not_supported":
      return { text: NOT_SUPPORTED_TEXT, link: null };
    case "no_qualifying_evidence": {
      const copy = NO_EVIDENCE_COPY[explanation.reason ?? "not_qualifying"];
      return { text: copy.text(liftName(explanation.benchmark_code, exerciseName)), link: copy.link };
    }
    default:
      // "recommended": the suggested weight is already in the load note.
      return null;
  }
}

export function LoadExplanation({
  explanation,
  exerciseName,
  onOpenAssess,
}: {
  explanation: LoadExplanationData | null | undefined;
  exerciseName: string;
  onOpenAssess: () => void;
}) {
  const wording = loadExplanationWording(explanation, exerciseName);
  if (!wording) return null;
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
      <span className="text-[11.5px] font-medium leading-[1.5] text-mute">{wording.text}</span>
      {wording.link && (
        <button
          type="button"
          onClick={onOpenAssess}
          className="text-[11.5px] font-semibold leading-[1.5] text-ac underline-offset-2 hover:underline"
        >
          {wording.link}
        </button>
      )}
    </div>
  );
}
