// src/perflab/prescription/WhyThisSession.tsx
//
// The live prescription explanation, shared by Planning and Twin.
//
// Three honesty rules shape it:
//
//   • Words come from the backend. `why.constraint_details` carries a reviewed label for every
//     code in `why.constraints_applied` (app/logic/constraint_labels.py); this component never
//     parses an engine code. A prescription stored before labels existed shows one neutral line
//     instead of raw codes.
//   • Say what is true, no more. When no fatigue or tissue rule fired, the lead says exactly that
//     — it does not call the state normal. What has not been measured is listed separately, so a
//     reassurance can never hide a gap.
//   • `why.state_evidence` REPLACES the driver phrases when present, never doubles them: both
//     carry the same drivers (prescription_finalize.py `_DRIVER_RULES` builds both from one row).

import { axisLabel as axisLabelOf, BAND, BAND_CHIP, narrowStatus } from "./axes";
import type { ConfidenceStatus, PrescriptionConfidence, StateEvidence, WorkoutPrescription } from "@/types";

type Why = NonNullable<WorkoutPrescription["why"]>;
type Detail = NonNullable<Why["constraint_details"]>[number];

/** Shown when state exists and no fatigue or tissue rule fired. */
export const NO_ADJUSTMENT_LEAD = "No additional adjustment from the available fatigue and tissue signals.";

/** Shown for a prescription stored before labels existed, instead of its raw codes. */
export const UNLABELLED_RULES_LINE = "Planning rules were applied to this session.";

/** Groups in reading order: what replaced or ruled out work first, then what tuned it. */
const GROUP_ORDER: Detail["group"][] = [
  "safety",
  "plan_rule",
  "state",
  "block",
  "objective",
  "adherence",
  "weak_point",
  "equipment",
  "other",
];

const BAND_ORDER: ConfidenceStatus[] = ["insufficient", "provisional", "established"];

/** Two-space-tolerant number formatting: thresholds are round, readings usually are not. */
const fmt = (n: number): string => (Number.isInteger(n) ? String(n) : n.toFixed(1));

/** "A", "A and B", "A, B and C". */
function joinWords(words: string[]): string {
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

function WhySection({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-[6px] font-mono text-[9.5px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">
        {label}
      </div>
      {children}
    </div>
  );
}

/**
 * One threshold test, with the reading that fired it.
 *
 * `confidence_status` being absent is NOT high certainty — the engine models no variance
 * for fatigue, tissue or skill at all — so a missing band renders no chip rather than an
 * optimistic one.
 */
function EvidenceRow({ ev }: { ev: StateEvidence }) {
  const band = ev.confidence_status == null ? null : BAND[narrowStatus(ev.confidence_status)];
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-[3px]">
      <span className="text-[12.5px] font-medium leading-[1.5] text-mute">{ev.label}</span>
      <span className="flex items-baseline gap-2">
        <span className="font-mono text-[12px] font-semibold leading-none text-soft">{fmt(ev.value)}</span>
        <span className="font-mono text-[10px] leading-none text-dim">
          {ev.direction === "above" ? "＞" : "＜"} {fmt(ev.threshold)}
        </span>
        {band && <span className={`${BAND_CHIP} ${band.cls}`}>{band.label}</span>}
      </span>
    </li>
  );
}

function DetailList({ details }: { details: Detail[] }) {
  return (
    <ul className="flex flex-col gap-[6px]">
      {details.map((d, i) => (
        <li
          key={`${d.code}-${i}`}
          className={`text-[12.5px] font-medium leading-[1.5] ${d.group === "safety" ? "text-warn" : "text-mute"}`}
        >
          {d.label}
        </li>
      ))}
    </ul>
  );
}

/**
 * How sure the twin is of the state the plan was built on. Collapsed: the least certain axis
 * is the summary; expanded, one row per band. Families the engine keeps no variance for are
 * named — as having no estimate, which is not the same as being unreliable.
 */
function ConfidenceBlock({ confidence }: { confidence: PrescriptionConfidence }) {
  const axes = Object.entries(confidence.capacity_axes ?? {});
  const weakest = confidence.weakest_capacity_axis;
  const weakestBand =
    confidence.weakest_capacity_status == null ? null : BAND[narrowStatus(confidence.weakest_capacity_status)];
  const notModelled = (confidence.uncertainty_not_modelled ?? []).map((f) => axisLabelOf(f).toLowerCase());

  const byBand: Record<ConfidenceStatus, string[]> = { insufficient: [], provisional: [], established: [] };
  for (const [axis, status] of axes) byBand[narrowStatus(status)].push(axisLabelOf(axis));

  let notModelledLine: string | null = null;
  if (notModelled.length > 0) {
    const sentence = joinWords(notModelled);
    notModelledLine = `${sentence.charAt(0).toUpperCase()}${sentence.slice(1)} ${
      notModelled.length === 1 ? "has" : "have"
    } no uncertainty estimate yet.`;
  }

  return (
    <details className="group">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono text-[9.5px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">
          How sure the twin is
        </span>
        {weakest != null && weakestBand && (
          <span className="text-[12px] font-medium leading-[1.5] text-mute">
            Least certain: <span className="font-semibold text-soft">{axisLabelOf(weakest)}</span>{" "}
            <span className={`${BAND_CHIP} ${weakestBand.cls} align-[1px]`}>{weakestBand.label}</span>
          </span>
        )}
      </summary>
      <div className="mt-[8px] flex flex-col gap-[6px]">
        {BAND_ORDER.filter((band) => byBand[band].length > 0).map((band) => (
          <div key={band} className="flex flex-wrap items-baseline gap-2 text-[12px] font-medium leading-[1.5] text-mute">
            <span className={`${BAND_CHIP} ${BAND[band].cls}`}>{BAND[band].label}</span>
            <span>{byBand[band].join(", ")}</span>
          </div>
        ))}
        {notModelledLine && <div className="font-mono text-[10px] leading-[1.5] text-dim">{notModelledLine}</div>}
      </div>
    </details>
  );
}

export function WhyThisSession({ why }: { why?: WorkoutPrescription["why"] }) {
  if (!why) return null;

  const evidence = why.state_evidence ?? [];
  const drivers = why.state_drivers ?? [];
  const codes = why.constraints_applied ?? [];
  const details = why.constraint_details ?? [];
  const goal = why.goal_alignment?.trim() ?? "";
  const confidence = why.confidence ?? null;

  const rank = (d: Detail) => {
    const i = GROUP_ORDER.indexOf(d.group);
    return i === -1 ? GROUP_ORDER.length : i;
  };
  const shaped = details
    .filter((d) => d.athlete_visible && d.group !== "advisory" && d.group !== "internal")
    .map((d, i) => ({ d, i }))
    .sort((a, b) => rank(a.d) - rank(b.d) || a.i - b.i)
    .map(({ d }) => d);
  const advisories = details.filter((d) => d.athlete_visible && d.group === "advisory");
  const unlabelled = details.length === 0 && codes.length > 0;

  const unmeasured =
    confidence == null
      ? []
      : Object.entries(confidence.capacity_axes ?? {})
          .filter(([, status]) => narrowStatus(status) === "insufficient")
          .map(([axis]) => axisLabelOf(axis));

  const hasConfidence =
    confidence != null &&
    (Object.keys(confidence.capacity_axes ?? {}).length > 0 ||
      confidence.weakest_capacity_axis != null ||
      (confidence.uncertainty_not_modelled ?? []).length > 0);

  const showsLead = evidence.length === 0 && confidence != null;
  const showsDriverPhrases = evidence.length === 0 && confidence == null && drivers.length > 0;

  if (
    !goal &&
    evidence.length === 0 &&
    !showsLead &&
    !showsDriverPhrases &&
    shaped.length === 0 &&
    advisories.length === 0 &&
    !unlabelled &&
    !hasConfidence
  ) {
    return null;
  }

  return (
    <div className="rounded-[12px] border border-ac/[0.18] bg-ac/[0.05] p-[16px]">
      <div className="mb-3 flex items-center gap-2 font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.1em] text-ac">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2v6M12 22v-2M5 12H2M22 12h-3" /><circle cx="12" cy="12" r="4" /></svg>
        Why this session
      </div>
      <div className="flex flex-col gap-[14px]">
        {(goal || showsLead) && (
          <div className="flex flex-col gap-[4px]">
            {goal && <div className="text-[13px] font-semibold leading-[1.5] text-soft">Built for {goal}.</div>}
            {showsLead && <div className="text-[12.5px] font-medium leading-[1.5] text-mute">{NO_ADJUSTMENT_LEAD}</div>}
          </div>
        )}

        {evidence.length > 0 && (
          <WhySection label="Adjusted for">
            <ul className="flex flex-col gap-[8px]">
              {evidence.map((ev, i) => (
                <EvidenceRow key={`${ev.axis}-${i}`} ev={ev} />
              ))}
            </ul>
          </WhySection>
        )}

        {showsDriverPhrases && (
          <WhySection label="State drivers">
            <ul className="flex flex-col gap-[6px]">
              {drivers.map((d, i) => (
                <li key={i} className="text-[12.5px] font-medium leading-[1.5] text-mute">{d}</li>
              ))}
            </ul>
          </WhySection>
        )}

        {unmeasured.length > 0 && (
          <div className="text-[12px] font-medium leading-[1.5] text-mute">
            Not yet measured: <span className="text-soft">{joinWords(unmeasured)}</span>
          </div>
        )}

        {(shaped.length > 0 || unlabelled) && (
          <WhySection label="What shaped this session">
            {shaped.length > 0 ? (
              <DetailList details={shaped} />
            ) : (
              <div className="text-[12.5px] font-medium leading-[1.5] text-mute">{UNLABELLED_RULES_LINE}</div>
            )}
          </WhySection>
        )}

        {advisories.length > 0 && (
          <WhySection label="Noted, not applied">
            <DetailList details={advisories} />
          </WhySection>
        )}

        {confidence != null && hasConfidence && <ConfidenceBlock confidence={confidence} />}
      </div>
    </div>
  );
}
