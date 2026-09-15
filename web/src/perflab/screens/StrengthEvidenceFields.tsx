// src/perflab/screens/StrengthEvidenceFields.tsx
//
// The one form for reporting strength on a canonical lift (S2): how the number was obtained,
// the weight or the set, and the date performed. Assess, onboarding and Settings all render
// this, and all build their request through strengthEvidenceBody.ts, so there is one way to
// state a strength fact. Weights are typed in the selected unit; the body builder converts to
// kilograms. Deliberately no eligibility messaging here — the explanation of a missing weight
// lives beside the prescribed exercise.
import { InfoTip, type InfoSection } from "../InfoTip";
import type { StrengthForm, StrengthMethod, WeightUnit } from "./strengthEvidenceBody";

const inputCls =
  "w-full rounded-[10px] border border-white/10 bg-panel px-3 py-2 text-[14px] text-ink font-mono";

const METHOD_CHOICES: { value: StrengthMethod; label: string }[] = [
  { value: "tested_max", label: "Tested 1-rep max" },
  { value: "rep_set", label: "A set I did" },
  { value: "estimate", label: "My estimate" },
];

// Help text states what the server does with each kind of report (strength_evidence_service,
// prescription_evidence) — nothing more. "28 days" mirrors the backend's
// STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS; "RPE 8" is the set-level qualifying gate
// (app/logic/strength_evidence.py). Change them together.
const METHOD_HELP: InfoSection[] = [
  {
    heading: "Tested 1-rep max",
    text: "A single rep at the heaviest weight you actually lifted. With its date, and done within the last 28 days, it can guide weight recommendations.",
  },
  {
    heading: "A set I did",
    text: "The load and reps of a set, plus its effort (RPE) if you know it. A dated set of 1–5 reps at RPE 8 or higher, done within the last 28 days, can guide weight recommendations.",
  },
  { heading: "My estimate", text: "Your own estimate. It's saved, but it isn't used to recommend weights." },
];

const RPE_HELP: InfoSection[] = [
  {
    text: "Rate of perceived exertion: how hard the set was, from 1 to 10, where 10 means you could not have done another rep. Leave it empty if you don't know — it is never guessed.",
  },
];

const DATE_HELP: InfoSection[] = [
  {
    text: "The day you did it. Without a date the result is still saved, but it can't guide weight recommendations; a result older than 28 days stops guiding them.",
  },
];

const helpRowCls = "flex items-center gap-1 text-[11px] font-medium leading-none text-dim";

export function StrengthEvidenceFields({
  form,
  onChange,
  liftLabel,
  dateRequired = false,
  unit = "kg",
}: {
  form: StrengthForm;
  onChange: (next: StrengthForm) => void;
  /** Names the lift in accessible labels when several lifts share a screen. */
  liftLabel?: string;
  /** Marks the date as required for a tested max or a set (onboarding). */
  dateRequired?: boolean;
  /** The unit weights are typed in. */
  unit?: WeightUnit;
}) {
  const set = (patch: Partial<StrengthForm>) => onChange({ ...form, ...patch });
  const prefix = liftLabel ? `${liftLabel}: ` : "";
  const needsDate = dateRequired && form.method !== "estimate";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
      <div
        role="radiogroup"
        aria-label={`${prefix}How did you get this number?`}
        className="flex flex-wrap gap-2"
      >
        {METHOD_CHOICES.map((choice) => {
          const active = form.method === choice.value;
          return (
            <button
              key={choice.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => set({ method: choice.value })}
              className={`rounded-[9px] border px-3 py-[7px] text-[12px] font-semibold leading-none ${
                active ? "border-ac/45 bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute"
              }`}
            >
              {choice.label}
            </button>
          );
        })}
      </div>
        <InfoTip label={`${prefix}About these options`} sections={METHOD_HELP} />
      </div>
      {form.method === "rep_set" ? (
        <>
        <div className="grid grid-cols-3 gap-2">
          <input
            value={form.load}
            onChange={(e) => set({ load: e.target.value })}
            inputMode="decimal"
            placeholder={`Load (${unit})`}
            aria-label={`${prefix}Load in ${unit}`}
            className={inputCls}
          />
          <input
            value={form.reps}
            onChange={(e) => set({ reps: e.target.value })}
            inputMode="numeric"
            placeholder="Reps"
            aria-label={`${prefix}Reps`}
            className={inputCls}
          />
          <input
            value={form.rpe}
            onChange={(e) => set({ rpe: e.target.value })}
            inputMode="decimal"
            placeholder="RPE 1–10 (optional)"
            aria-label={`${prefix}Effort as RPE 1 to 10, optional`}
            className={inputCls}
          />
        </div>
        <div className={helpRowCls}>
          <span>Effort (RPE) is 1 to 10, optional</span>
          <InfoTip label={`${prefix}About RPE`} sections={RPE_HELP} />
        </div>
        </>
      ) : (
        <input
          value={form.weight}
          onChange={(e) => set({ weight: e.target.value })}
          inputMode="decimal"
          placeholder={`Weight (${unit})`}
          aria-label={`${prefix}Weight in ${unit}`}
          className={inputCls}
        />
      )}
      <div className="flex flex-col gap-1">
        <div className={helpRowCls}>
          <span>{needsDate ? "Date performed (required)" : "Date performed"}</span>
          <InfoTip label={`${prefix}About the date`} sections={DATE_HELP} />
        </div>
        <input
          type="date"
          value={form.performedOn}
          onChange={(e) => set({ performedOn: e.target.value })}
          aria-label={`${prefix}Date performed`}
          className={inputCls}
        />
      </div>
    </div>
  );
}
