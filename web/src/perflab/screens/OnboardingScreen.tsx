// src/perflab/screens/OnboardingScreen.tsx
import { useState } from "react";
import { useAuth } from "@/auth/useAuth";
import { isImperial, kgToLbs, lbsToKg, parseMMSS, weightLabel } from "@/lib/units";
import { createObjective } from "@/api/perfLabClient";
import type {
  ApiError,
  OnboardResponse,
  OnboardStrengthReport,
  RunTimeTrialReport,
  Vo2EstimateRead,
} from "@/types";
import { isRunningGoal, isStrengthGoal, TRAINING_GOALS, usePerfLab } from "../store";
import { getGoalLoadDefinition } from "../goalLoadDefinitions";
import { DOMAIN_OPTIONS, domainLabel } from "../domains";
import { EQUIPMENT_TAGS, MODE_OPTIONS, type EquipmentMode } from "../equipment";
import { CANONICAL_LIFTS } from "./canonicalLifts";
import { StrengthEvidenceFields } from "./StrengthEvidenceFields";
import { underivedReportMessage } from "./strengthOutcome";
import {
  EMPTY_STRENGTH_FORM,
  isBlankStrengthForm,
  strengthReport,
  type StrengthForm,
  type WeightUnit,
} from "./strengthEvidenceBody";

const labelCls = "font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.1em] text-[#9aa0ab]";
const inputCls = "mt-[9px] w-full rounded-[11px] border border-white/10 bg-panel px-[14px] py-3 text-[14px] text-ink";
const segOn = "rounded-[11px] border border-ac/40 bg-ac/[0.12] p-3 text-center text-[13px] font-semibold leading-none text-ac";
const segOff = "rounded-[11px] border border-white/10 bg-panel p-3 text-center text-[13px] font-semibold leading-none text-mute";
const btnPrimary = "rounded-[11px] bg-ac px-6 py-[13px] text-[13.5px] font-semibold leading-none text-[#0a0c10]";
const btnBack = "rounded-[11px] border border-white/[0.12] px-[22px] py-[13px] text-[13.5px] font-semibold leading-none text-mute";

const EXPERIENCE_LEVELS = ["beginner", "intermediate", "advanced"];

const STEPS = [
  ["01", "Profile", "Name and date of birth — the basics."],
  ["02", "Training context", "Goal, schedule and equipment."],
  ["03", "Seed your twin", "Baseline inputs to initialize S(t)."],
];

function Seg({ options, value, onChange }: { options: readonly string[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="mt-[9px] grid grid-cols-2 gap-2">
      {options.map((opt) => (
        <button key={opt} type="button" aria-pressed={value === opt} onClick={() => onChange(opt)}
          className={`w-full ${value === opt ? segOn : segOff}`}>
          {opt}
        </button>
      ))}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className={labelCls}>{label}</span>{children}</label>;
}

const anchorRowCls = "flex flex-col gap-[3px] border-b border-white/[0.05] py-[10px] last:border-0";
const anchorLabelCls = "font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.13em] text-faint";
const anchorValCls = "text-[12px] font-medium leading-[1.5] text-mute";

function GoalAnchorCard({ goal }: { goal: string }) {
  const [open, setOpen] = useState(false);
  const defn = getGoalLoadDefinition(goal);
  if (!defn) return null;
  return (
    <div className="mt-5 rounded-[12px] border border-white/[0.08] bg-white/[0.02]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-3"
      >
        <span className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-mute">
          Baseline anchors · {goal}
        </span>
        <span className="font-mono text-[11px] leading-none text-faint">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="border-t border-white/[0.06] px-4 pb-3">
          <div className={anchorRowCls}>
            <span className={anchorLabelCls}>Training load</span>
            <span className={anchorValCls}>{defn.goalSpecificTrainingLoad}</span>
          </div>
          <div className={anchorRowCls}>
            <span className={anchorLabelCls}>Capacity anchor</span>
            <span className={anchorValCls}>{defn.primaryCapacityAnchor}</span>
          </div>
          <div className={anchorRowCls}>
            <span className={anchorLabelCls}>Load-tolerance anchor</span>
            <span className={anchorValCls}>{defn.loadToleranceAnchor}</span>
          </div>
          <div className={anchorRowCls}>
            <span className={anchorLabelCls}>Risk / tissue anchor</span>
            <span className={anchorValCls}>{defn.riskOrTissueAnchor}</span>
          </div>
          <div className={anchorRowCls}>
            <span className={anchorLabelCls}>Best retest metric</span>
            <span className={anchorValCls}>{defn.bestRetestMetric}</span>
          </div>
        </div>
      )}
    </div>
  );
}

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

/** The saved 1.5 mi run and its display-only VO₂max estimate (B2). No grade is shown: the old
 *  grade tables were unsourced. Outside the study population the label says so. */
function SavedRunCard({ run }: { run: Vo2EstimateRead }) {
  const estimated = run.status === "estimated" && run.vo2max_ml_kg_min != null;
  return (
    <section aria-label="Your 1.5 mi run" className="mb-6 mt-5 rounded-[12px] border border-white/[0.08] px-4 py-4">
      <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-mute">
        1.5 mi run · {mmss(run.elapsed_seconds)}
      </div>
      {estimated ? (
        <>
          <div className="mt-3 font-mono text-[26px] font-semibold leading-none text-ink">
            {run.vo2max_ml_kg_min!.toFixed(1)} <span className="text-[13px] text-faint">ml·kg⁻¹·min⁻¹</span>
          </div>
          <p className="m-0 mt-2 text-[13px] font-semibold leading-[1.5] text-soft">{run.headline}</p>
          <p className={`m-0 mt-1 text-[12px] font-medium leading-[1.5] ${run.extrapolated ? "text-hot" : "text-mute"}`}>
            {run.population_label}
          </p>
          <p className="m-0 mt-1 text-[11.5px] font-medium leading-[1.5] text-faint">
            Typical error of this estimate: ±{run.see_ml_kg_min} ml·kg⁻¹·min⁻¹ (George et al., 1993).
          </p>
        </>
      ) : (
        <p className="m-0 mt-2 text-[12.5px] font-medium leading-[1.5] text-soft">{run.headline}</p>
      )}
    </section>
  );
}

export function OnboardingScreen() {
  const { state, actions } = usePerfLab();
  const { completeOnboarding, token } = useAuth();
  const [seeding, setSeeding] = useState(false);
  const [domains, setDomains] = useState<string[]>([]); // extra domains → Objectives
  const toggleDomain = (d: string) =>
    setDomains((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d]));

  const units = state.settings.units;
  const goal = state.settings.goal;
  const imperial = isImperial(units);
  const ob = state.obStep;
  const goOverview = () => actions.setScreen("overview");

  // Step 1 — profile
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [dob, setDob] = useState(""); // ISO "YYYY-MM-DD" from <input type="date">

  const todayISO = new Date().toISOString().slice(0, 10);
  const dobAge = dob ? Math.floor((Date.now() - new Date(`${dob}T00:00:00`).getTime()) / 3.15576e10) : null;
  const dobError: string | null =
    !dob ? null
    : Number.isNaN(new Date(`${dob}T00:00:00`).getTime()) ? "Enter a valid date."
    : dob > todayISO ? "Date of birth can't be in the future."
    : dobAge !== null && (dobAge < 5 || dobAge > 100) ? "Enter a realistic date of birth."
    : null;

  // Step 2 — training context
  const [daysPerWeek, setDaysPerWeek] = useState("4");
  const [sessionDur, setSessionDur] = useState("60");
  // Equipment is one of the four basics the prescribe gate requires (onboarding_state.py:72).
  // "Not set" and "bodyweight only" are different answers, so the mode is explicit rather than
  // inferred from an empty list.
  const [equipMode, setEquipMode] = useState<EquipmentMode>("unset");
  const [equipTags, setEquipTags] = useState<string[]>([]);
  const equipment: string[] =
    equipMode === "bodyweight" ? ["bodyweight"] : equipMode === "equipment" ? equipTags : [];
  const toggleEquipTag = (tag: string) =>
    setEquipTags((cur) => (cur.includes(tag) ? cur.filter((t) => t !== tag) : [...cur, tag]));

  // Step 3 — optional profile context. Stored on the profile; no engine reads these yet, and
  // the copy says exactly that rather than implying they shape the plan.
  const [experienceLevel, setExperienceLevel] = useState("");
  const [experienceYears, setExperienceYears] = useState("");
  const [heightCm, setHeightCm] = useState("");
  const [overhead, setOverhead] = useState("");
  const [pullups, setPullups] = useState("");

  // Step 3 — running seed: a maximal 1.5 mi run (B2). Nothing is pre-filled: a time, a date or a
  // sex the athlete did not give must not reach the record. Body mass is the bodyweight field.
  const [t15, setT15] = useState("");
  const [runDate, setRunDate] = useState(""); // ISO "YYYY-MM-DD"
  // The estimate model's sex input, asked here and sent only with the run. Unset is an answer:
  // the run is saved without an estimate.
  const [estimateSex, setEstimateSex] = useState<"" | "Female" | "Male">("");

  // Step 3 — strength / general seed
  const [bodyweight, setBodyweight] = useState("");
  // Squat / bench / deadlift are characterized strength reports (S2): how the number was
  // obtained, the weight or the set, and the date — never bare numbers. Typed in the selected
  // unit; strengthReport converts to kilograms for the request.
  const [strength, setStrength] = useState<Record<string, StrengthForm>>(() =>
    Object.fromEntries(CANONICAL_LIFTS.map((lift) => [lift.code, EMPTY_STRENGTH_FORM])),
  );
  const [finishError, setFinishError] = useState<string | null>(null);
  // Strength reports onboarding kept without an estimate (P4-3). When there are any, the athlete
  // reads them here, after saving, and continues when ready; otherwise this stays null.
  const [keptReports, setKeptReports] = useState<{ label: string; message: string }[] | null>(null);
  // The 1.5 mi run the server saved, with its display-only estimate (B2). When there is one, the
  // athlete reads it on the saved screen before continuing.
  const [savedRun, setSavedRun] = useState<Vo2EstimateRead | null>(null);
  const showSaved = keptReports !== null || savedRun !== null;
  const [run5k, setRun5k] = useState("");

  const wUnit = weightLabel(units);
  const unit: WeightUnit = imperial ? "lb" : "kg";

  // When user toggles units, convert every entered weight — bodyweight, and each lift's
  // weight and set load — so the digits on screen keep describing the same mass.
  function handleUnitsChange(v: string) {
    // Seg also reports a click on the option already selected; converting then would turn a
    // kilogram figure into its pound figure while the unit stayed kilograms.
    if (v === units) return;
    actions.setSetting("units", v);
    const toImperial = v === "Imperial (mi)";
    const convert = toImperial
      ? (s: string) => { const n = parseFloat(s); return isNaN(n) ? s : kgToLbs(n).toFixed(0); }
      : (s: string) => { const n = parseFloat(s); return isNaN(n) ? s : lbsToKg(n).toFixed(1); };
    if (bodyweight) setBodyweight(convert(bodyweight));
    setStrength((cur) =>
      Object.fromEntries(
        Object.entries(cur).map(([code, f]) => [
          code,
          { ...f, weight: f.weight ? convert(f.weight) : f.weight, load: f.load ? convert(f.load) : f.load },
        ]),
      ),
    );
    // Height is typed in cm or inches depending on the unit, so it converts with the rest.
    const h = parseFloat(heightCm);
    if (!isNaN(h)) setHeightCm(toImperial ? (h / 2.54).toFixed(1) : (h * 2.54).toFixed(0));
    if (overhead) setOverhead(convert(overhead));
  }

  /** Each report the server kept without an estimate — including one an earlier, interrupted
   *  attempt already kept — named by its lift. */
  function keptReportsOf(response: OnboardResponse | undefined) {
    return (response?.strength ?? []).flatMap((outcome) => {
      const report = outcome.report;
      if (report == null) return [];
      const lift = CANONICAL_LIFTS.find((l) => l.code === report.benchmark_code);
      return [{ label: lift?.label ?? report.benchmark_code, message: underivedReportMessage(report, unit) }];
    });
  }

  async function finish() {
    if (seeding) return;
    // Build the strength reports before sending anything: an incomplete or impossible report
    // is caught on this step, where the athlete can fix it.
    const reports: OnboardStrengthReport[] = [];
    if (isStrengthGoal(goal)) {
      for (const lift of CANONICAL_LIFTS) {
        const liftForm = strength[lift.code];
        if (isBlankStrengthForm(liftForm)) continue;
        const built = strengthReport(lift.code, liftForm, { requireDate: true, unit });
        if (!built.ok) {
          setFinishError(`${lift.label}: ${built.error}`);
          return;
        }
        reports.push(built.report);
      }
    }
    // The 1.5 mi run, checked here too: a time the server cannot hold is fixed on this step.
    let runTrial: RunTimeTrialReport | undefined;
    if (isRunningGoal(goal) && t15.trim()) {
      const s = parseMMSS(t15.trim());
      if (s === null || s < 180 || s > 14400) {
        setFinishError("1.5 mi time: enter it as MM:SS, between 3:00 and 240:00.");
        return;
      }
      runTrial = { elapsed_seconds: s };
      if (runDate) runTrial.performed_on = runDate;
      const mass = parseFloat(bodyweight);
      if (!isNaN(mass) && mass > 0) {
        // Sent as entered, with its unit; the server converts pounds exactly.
        runTrial.body_mass = mass;
        runTrial.body_mass_unit = unit;
      }
      if (estimateSex) runTrial.sex_for_estimate = estimateSex === "Male" ? "male" : "female";
    }
    setFinishError(null);
    setSeeding(true);
    let saved = false;
    let kept: { label: string; message: string }[] = [];
    let run: Vo2EstimateRead | null = null;
    try {
      const req: Record<string, unknown> = { goal };

      const displayName = `${firstName.trim()} ${lastName.trim()}`.trim();
      if (displayName) req.display_name = displayName;
      if (dob && !dobError) req.date_of_birth = dob;

      // B2: the run is saved by /v1/onboard as a time trial; the VO₂max estimate, when its inputs
      // are given, is made and stored server-side. No call to the deprecated /compute-metrics.
      if (runTrial) req.run_time_trial = runTrial;

      const parseWeight = (s: string) => {
        const n = parseFloat(s);
        if (isNaN(n) || n <= 0) return undefined;
        return imperial ? lbsToKg(n) : n;
      };

      // Training context the athlete answered on step 2. These used to be collected and then
      // dropped on the floor, which is why `can_prescribe` stayed false right after onboarding:
      // the hard gate wants equipment, and nothing ever sent it.
      req.equipment = equipment;
      const days = parseInt(daysPerWeek, 10);
      if (!isNaN(days) && days >= 1 && days <= 7) req.available_days_per_week = days;
      const dur = parseInt(sessionDur, 10);
      if (!isNaN(dur) && dur >= 1) req.session_duration_minutes = dur;

      if (bodyweight) req.bodyweight_kg = parseWeight(bodyweight);
      if (reports.length) req.strength = reports;
      if (run5k) { const s = parseMMSS(run5k); if (s) req.run_5k_seconds = s; }
      // The profile has a column for the 1.5 mi time too, so it is kept there as well.
      if (runTrial) req.run_1p5mi_seconds = runTrial.elapsed_seconds;

      // Optional context — omitted fields are left alone by the route, never cleared.
      if (experienceLevel) req.experience_level = experienceLevel;
      const years = parseFloat(experienceYears);
      if (!isNaN(years) && years >= 0) req.experience_years = years;
      const height = parseFloat(heightCm);
      if (!isNaN(height) && height > 0) req.height_cm = imperial ? height * 2.54 : height;
      if (overhead) { const kg = parseWeight(overhead); if (kg) req.overhead_1rm_kg = kg; }
      const pull = parseInt(pullups, 10);
      if (!isNaN(pull) && pull >= 0) req.pullup_max_reps = pull;

      const response = await completeOnboarding(req);
      saved = true;
      kept = keptReportsOf(response);
      run = response?.run_time_trial ?? null;

      // Each picked domain becomes an Objective (drives plan emphasis + the Assess
      // filter). Best-effort + only for a signed-in user; a failure never blocks entry.
      if (token && domains.length) {
        await Promise.all(
          domains.map((d, i) =>
            createObjective({ label: domainLabel(d), domain: d, priority: Math.min(i + 1, 5) }, token)
              .catch(() => undefined),
          ),
        );
      }
    } catch (e) {
      // Nothing was saved: say so and stay on this step, where the athlete can retry.
      const msg = (e as ApiError)?.message;
      setFinishError(typeof msg === "string" ? msg : "Couldn't save your baseline — try again.");
    } finally {
      setSeeding(false);
      if (saved) {
        if (kept.length > 0 || run !== null) {
          setKeptReports(kept);
          setSavedRun(run);
        } else goOverview();
      }
    }
  }

  return (
    <div className="grid min-h-screen grid-cols-1 md:grid-cols-[minmax(0,440px)_1fr]">
      {/* brand panel */}
      <div className="flex flex-col justify-between border-r border-white/[0.06] px-11 py-12" style={{ background: "radial-gradient(120% 80% at 0% 0%,#11321f,#0b0e13 55%)" }}>
        <div className="flex items-center gap-3">
          <div className="grid h-[34px] w-[34px] place-items-center rounded-[10px] bg-gradient-to-br from-ac to-teal text-[17px] font-extrabold leading-none text-[#0a0c10]">◆</div>
          <div>
            <div className="text-[16px] font-bold leading-none text-ink">PERF LAB</div>
            <div className="mt-[3px] font-mono text-[10px] leading-[1.3] tracking-[0.14em] text-faint">PERFORMANCE OS</div>
          </div>
        </div>
        <div>
          <h2 className="m-0 text-[34px] font-bold leading-[1.1] tracking-[-0.03em] text-ink">Your training, modeled and prescribed.</h2>
          <div className="mt-[30px] flex flex-col gap-[18px]">
            {STEPS.map(([n, t, d]) => (
              <div key={n} className="flex gap-[13px]">
                <span className="font-mono text-[14px] font-bold leading-[1.4] text-ac">{n}</span>
                <div>
                  <div className="text-[14px] font-semibold leading-none text-ink">{t}</div>
                  <div className="mt-[5px] text-[12.5px] font-medium leading-[1.5] text-mute">{d}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="font-mono text-[11px] leading-none text-dim">v0.3 · perf-lab-web</div>
      </div>

      {/* form panel */}
      <div className="flex max-w-[620px] flex-col justify-center px-14 py-12">
        <div className="mb-2 flex items-center justify-between">
          <span className="font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.18em] text-faint">Step {ob} of 3</span>
          <button onClick={goOverview} className="border-0 bg-transparent text-[12px] font-medium leading-none text-mute">Skip for now →</button>
        </div>
        <div className="mb-[34px] h-1 overflow-hidden rounded-full bg-white/[0.07]">
          <div className="h-full rounded-full transition-all duration-300" style={{ width: ob === 1 ? "33%" : ob === 2 ? "66%" : "100%", background: "linear-gradient(90deg,var(--ac),#7bd6c0)" }} />
        </div>

        {/* ── Step 1: profile ── */}
        {showSaved && (
          <div>
            <h1 className="m-0 text-[30px] font-bold leading-[1.1] tracking-[-0.025em] text-ink">Your baseline is saved</h1>
            {savedRun !== null && <SavedRunCard run={savedRun} />}
            {keptReports !== null && keptReports.length > 0 && (
              <>
                <p className="m-0 mb-6 mt-3 text-[14px] font-medium leading-[1.5] text-mute">
                  Some of the sets you reported were recorded without a 1RM estimate.
                </p>
                <ul aria-label="Recorded without an estimate" className="m-0 flex list-none flex-col gap-3 p-0">
                  {keptReports.map((kept) => (
                    <li key={kept.label} className="rounded-[11px] border border-white/[0.08] px-4 py-3">
                      <div className="text-[13px] font-semibold leading-none text-ink">{kept.label}</div>
                      <div className="mt-2 text-[12.5px] font-medium leading-[1.5] text-soft">{kept.message}</div>
                    </li>
                  ))}
                </ul>
              </>
            )}
            <div className="mt-[30px] flex justify-end">
              <button onClick={goOverview} className="rounded-[11px] bg-gradient-to-r from-ac to-[#a7e36e] px-[26px] py-[13px] text-[13.5px] font-semibold leading-none text-[#0a0c10]">
                Continue
              </button>
            </div>
          </div>
        )}

        {!showSaved && ob === 1 && (
          <div>
            <h1 className="m-0 text-[30px] font-bold leading-[1.1] tracking-[-0.025em] text-ink">Let's set up your profile</h1>
            <p className="m-0 mb-7 mt-3 text-[14px] font-medium leading-[1.5] text-mute">Just the basics. You can change any of this later.</p>
            <div className="grid grid-cols-2 gap-4">
              <Field label="First name"><input value={firstName} onChange={(e) => setFirstName(e.target.value)} placeholder="First name" className={inputCls} /></Field>
              <Field label="Last name"><input value={lastName} onChange={(e) => setLastName(e.target.value)} placeholder="Last name" className={inputCls} /></Field>
              <Field label="Date of birth">
                <input type="date" value={dob} max={todayISO} onChange={(e) => setDob(e.target.value)}
                  className={inputCls} style={{ colorScheme: "dark" }} />
                {dobError && <span className="mt-[6px] block text-[11.5px] font-medium leading-none text-hot">{dobError}</span>}
              </Field>
              {/* Sex is asked on the running seed step instead, next to the field test that is
                  the only thing reading it. Asking here implied it shaped the plan; no profile
                  column stores it and no engine reads it. */}
            </div>
            <div className="mt-[30px] flex justify-end">
              <button onClick={actions.obNext} disabled={!!dobError} className={`${btnPrimary} disabled:opacity-50`}>Continue →</button>
            </div>
          </div>
        )}

        {/* ── Step 2: training context ── */}
        {!showSaved && ob === 2 && (
          <div>
            <h1 className="m-0 text-[30px] font-bold leading-[1.1] tracking-[-0.025em] text-ink">Training context</h1>
            <p className="m-0 mb-7 mt-3 text-[14px] font-medium leading-[1.5] text-mute">So the plan speaks your language.</p>
            <div className="flex flex-col gap-4">
              <Field label="Primary goal">
                <select value={goal} onChange={(e) => actions.setSetting("goal", e.target.value)} className={inputCls} style={{ colorScheme: "dark" }}>
                  {TRAINING_GOALS.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
                </select>
              </Field>
              <div className="block">
                <span className={labelCls}>What are you training for? (pick all that apply)</span>
                <p className="mb-2 mt-2 text-[11.5px] font-medium leading-[1.5] text-mute">Each becomes an objective — they drive what the plan emphasizes and which benchmarks Assess suggests.</p>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {DOMAIN_OPTIONS.map((d) => (
                    <button key={d.value} type="button" aria-pressed={domains.includes(d.value)}
                      onClick={() => toggleDomain(d.value)}
                      className={domains.includes(d.value) ? segOn : segOff}>
                      {d.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="block"><span className={labelCls}>Units</span>
                <Seg options={["Metric (km)", "Imperial (mi)"]} value={units} onChange={handleUnitsChange} />
              </div>
              {/* Asked for every goal: both are profile basics, and days/week is one of the
                  four the prescribe gate checks. The running path used to show weekly volume
                  here instead — collected, never sent, and no column to send it to. */}
              <div className="grid grid-cols-2 gap-4">
                <Field label="Training days / week">
                  <input value={daysPerWeek} onChange={(e) => setDaysPerWeek(e.target.value)} inputMode="numeric" className={inputCls} />
                </Field>
                <Field label="Session duration (min)">
                  <input value={sessionDur} onChange={(e) => setSessionDur(e.target.value)} inputMode="numeric" className={inputCls} />
                </Field>
              </div>

              <div className="block">
                <span className={labelCls}>Equipment you have</span>
                <p className="mb-2 mt-2 text-[11.5px] font-medium leading-[1.5] text-mute">
                  A hard filter on what can be prescribed — and required before your first session can be planned.
                </p>
                <div className="grid grid-cols-3 gap-2">
                  {MODE_OPTIONS.map((o) => (
                    <button key={o.mode} type="button" aria-pressed={equipMode === o.mode}
                      onClick={() => setEquipMode(o.mode)}
                      className={equipMode === o.mode ? segOn : segOff}>
                      {o.label}
                    </button>
                  ))}
                </div>
                <p className="mt-2 text-[11.5px] font-medium leading-[1.5] text-faint">
                  {MODE_OPTIONS.find((o) => o.mode === equipMode)?.help}
                </p>
                {equipMode === "equipment" && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {EQUIPMENT_TAGS.map((t) => (
                      <button key={t.tag} type="button" aria-pressed={equipTags.includes(t.tag)}
                        onClick={() => toggleEquipTag(t.tag)}
                        className={`rounded-[9px] border px-3 py-[7px] text-[12px] font-semibold leading-none ${
                          equipTags.includes(t.tag)
                            ? "border-ac/45 bg-ac/[0.12] text-ac"
                            : "border-white/10 bg-panel text-mute"
                        }`}>
                        {t.label}
                      </button>
                    ))}
                  </div>
                )}
                {equipMode === "equipment" && equipTags.length === 0 && (
                  <p className="mt-2 text-[11.5px] font-medium leading-[1.5] text-hot">
                    Pick at least one, or choose “Not set”.
                  </p>
                )}
              </div>
            </div>
            <div className="mt-[30px] flex justify-between">
              <button onClick={actions.obBack} className={btnBack}>← Back</button>
              <button onClick={actions.obNext} className={btnPrimary}>Continue →</button>
            </div>
          </div>
        )}

        {/* ── Step 3: seed your twin ── */}
        {!showSaved && ob === 3 && (
          <div>
            <h1 className="m-0 text-[30px] font-bold leading-[1.1] tracking-[-0.025em] text-ink">Seed your twin</h1>
            <p className="m-0 mb-7 mt-3 text-[14px] font-medium leading-[1.5] text-mute">
              {isRunningGoal(goal)
                ? "Enter a recent 1.5 mi run, as fast as you could go — or skip and add one later."
                : isStrengthGoal(goal)
                  ? "Enter your current lifts to seed the strength model — or skip and add after your first session."
                  : "Enter any baseline data you have — all fields are optional."}
            </p>

            {isRunningGoal(goal) && (
              <div className="flex flex-col gap-4">
                <div className="grid grid-cols-2 gap-4">
                  <Field label="1.5 mi time"><input value={t15} onChange={(e) => setT15(e.target.value)} placeholder="MM:SS" className={`${inputCls} font-mono`} /></Field>
                  <Field label="Date of the run">
                    <input type="date" value={runDate} max={todayISO} onChange={(e) => setRunDate(e.target.value)}
                      className={inputCls} style={{ colorScheme: "dark" }} />
                  </Field>
                  <Field label={`Bodyweight (${wUnit})`}><input value={bodyweight} onChange={(e) => setBodyweight(e.target.value)} inputMode="decimal" placeholder="—" className={`${inputCls} font-mono`} /></Field>
                </div>
                <div className="block">
                  <span className={labelCls}>Sex (for the estimate)</span>
                  <Seg options={["Female", "Male"]} value={estimateSex}
                    onChange={(v) => setEstimateSex(v === estimateSex ? "" : (v as "Female" | "Male"))} />
                  <p className="mt-2 text-[11.5px] font-medium leading-[1.5] text-faint">
                    Bodyweight and sex are used only for an estimated VO₂max from your 1.5 mi time, and
                    are kept with that run. Leave either out and the run is still saved, without an
                    estimate. The estimate is not used for your plan.
                  </p>
                </div>
              </div>
            )}

            {isStrengthGoal(goal) && (
              <div className="flex flex-col gap-5">
                <div className="grid grid-cols-2 gap-4">
                  <Field label={`Bodyweight (${wUnit})`}><input value={bodyweight} onChange={(e) => setBodyweight(e.target.value)} inputMode="decimal" placeholder="—" className={`${inputCls} font-mono`} /></Field>
                </div>
                {CANONICAL_LIFTS.map((lift) => (
                  <div key={lift.code} className="flex flex-col gap-2">
                    <span className={labelCls}>{lift.label}</span>
                    <StrengthEvidenceFields
                      form={strength[lift.code]}
                      onChange={(next) => setStrength((cur) => ({ ...cur, [lift.code]: next }))}
                      liftLabel={lift.label}
                      dateRequired
                      unit={unit}
                    />
                  </div>
                ))}
              </div>
            )}

            {!isRunningGoal(goal) && !isStrengthGoal(goal) && (
              <div className="grid grid-cols-2 gap-4">
                <Field label={`Bodyweight (${wUnit})`}><input value={bodyweight} onChange={(e) => setBodyweight(e.target.value)} inputMode="decimal" placeholder="—" className={`${inputCls} font-mono`} /></Field>
                <Field label="5K time (MM:SS)"><input value={run5k} onChange={(e) => setRun5k(e.target.value)} placeholder="—" className={`${inputCls} font-mono`} /></Field>
              </div>
            )}

            {/* Optional context. Experience seeds the starting twin; the rest is stored and
                read back in Settings — the copy promises storage, not influence, because no
                engine reads height, overhead or pull-ups today. */}
            <details className="mt-6 rounded-[12px] border border-white/[0.08] bg-white/[0.02]">
              <summary className="cursor-pointer px-4 py-3 font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-mute">
                More about you (optional)
              </summary>
              <div className="flex flex-col gap-4 border-t border-white/[0.06] px-4 py-4">
                <div className="block">
                  <span className={labelCls}>Training experience</span>
                  <div className="mt-[9px] grid grid-cols-3 gap-2">
                    {EXPERIENCE_LEVELS.map((lvl) => (
                      <button key={lvl} type="button" aria-pressed={experienceLevel === lvl}
                        onClick={() => setExperienceLevel(experienceLevel === lvl ? "" : lvl)}
                        className={experienceLevel === lvl ? segOn : segOff}>
                        {lvl[0].toUpperCase() + lvl.slice(1)}
                      </button>
                    ))}
                  </div>
                  <p className="mt-2 text-[11.5px] font-medium leading-[1.5] text-faint">
                    Shapes the starting twin. Left blank, you start as a beginner.
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Years training">
                    <input value={experienceYears} onChange={(e) => setExperienceYears(e.target.value)} inputMode="decimal" placeholder="—" className={`${inputCls} font-mono`} />
                  </Field>
                  <Field label={`Height (${imperial ? "in" : "cm"})`}>
                    <input value={heightCm} onChange={(e) => setHeightCm(e.target.value)} inputMode="decimal" placeholder="—" className={`${inputCls} font-mono`} />
                  </Field>
                  <Field label={`Overhead press 1RM (${wUnit})`}>
                    <input value={overhead} onChange={(e) => setOverhead(e.target.value)} inputMode="decimal" placeholder="—" className={`${inputCls} font-mono`} />
                  </Field>
                  <Field label="Max pull-ups">
                    <input value={pullups} onChange={(e) => setPullups(e.target.value)} inputMode="numeric" placeholder="—" className={`${inputCls} font-mono`} />
                  </Field>
                </div>
                <p className="text-[11.5px] font-medium leading-[1.5] text-faint">
                  Height, overhead press and pull-ups are saved to your profile. They are not used to
                  build your sessions yet.
                </p>
              </div>
            </details>

            <GoalAnchorCard goal={goal} />

            {finishError && (
              <div role="alert" className="mt-5 text-[12px] font-medium leading-[1.5] text-hot">{finishError}</div>
            )}

            <div className="mt-[30px] flex justify-between">
              <button onClick={actions.obBack} className={btnBack}>← Back</button>
              <button onClick={finish} disabled={seeding} className="rounded-[11px] bg-gradient-to-r from-ac to-[#a7e36e] px-[26px] py-[13px] text-[13.5px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60">
                {seeding ? "Seeding twin…" : "Enter Perf Lab →"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
