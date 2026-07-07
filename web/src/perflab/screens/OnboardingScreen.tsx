// src/perflab/screens/OnboardingScreen.tsx
import { useState } from "react";
import { useAuth } from "@/auth/useAuth";
import { isImperial, kgToLbs, lbsToKg, parseMMSS, weightLabel } from "@/lib/units";
import { computeMetrics } from "@/api/perfLabClient";
import { isRunningGoal, isStrengthGoal, TRAINING_GOALS, usePerfLab } from "../store";
import { getGoalLoadDefinition } from "../goalLoadDefinitions";

const labelCls = "font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.1em] text-[#9aa0ab]";
const inputCls = "mt-[9px] w-full rounded-[11px] border border-white/10 bg-panel px-[14px] py-3 text-[14px] text-ink";
const segOn = "rounded-[11px] border border-ac/40 bg-ac/[0.12] p-3 text-center text-[13px] font-semibold leading-none text-ac";
const segOff = "rounded-[11px] border border-white/10 bg-panel p-3 text-center text-[13px] font-semibold leading-none text-mute";
const btnPrimary = "rounded-[11px] bg-ac px-6 py-[13px] text-[13.5px] font-semibold leading-none text-[#0a0c10]";
const btnBack = "rounded-[11px] border border-white/[0.12] px-[22px] py-[13px] text-[13.5px] font-semibold leading-none text-mute";

const STEPS = [
  ["01", "Profile", "Name, sex, units — the basics."],
  ["02", "Training context", "Goal and current training load."],
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

export function OnboardingScreen() {
  const { state, actions } = usePerfLab();
  const { completeOnboarding } = useAuth();
  const [seeding, setSeeding] = useState(false);

  const sex = state.settings.sex;
  const units = state.settings.units;
  const goal = state.settings.goal;
  const imperial = isImperial(units);
  const ob = state.obStep;
  const goOverview = () => actions.setScreen("overview");

  // Step 1 — profile
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");

  // Step 2 — training context
  const [weeklyVol, setWeeklyVol] = useState(imperial ? "30" : "48");
  const [daysPerWeek, setDaysPerWeek] = useState("4");
  const [sessionDur, setSessionDur] = useState("60");

  // Step 3 — running seed
  const [t300, setT300] = useState("0:52");
  const [t15, setT15] = useState("9:18");

  // Step 3 — strength / general seed
  const [bodyweight, setBodyweight] = useState("");
  const [squat, setSquat] = useState("");
  const [bench, setBench] = useState("");
  const [deadlift, setDeadlift] = useState("");
  const [run5k, setRun5k] = useState("");

  const wUnit = weightLabel(units);

  // When user toggles units, convert any entered weight values
  function handleUnitsChange(v: string) {
    actions.setSetting("units", v);
    const toImperial = v === "Imperial (mi)";
    const convert = toImperial
      ? (s: string) => { const n = parseFloat(s); return isNaN(n) ? s : kgToLbs(n).toFixed(0); }
      : (s: string) => { const n = parseFloat(s); return isNaN(n) ? s : lbsToKg(n).toFixed(1); };
    if (bodyweight) setBodyweight(convert(bodyweight));
    if (squat) setSquat(convert(squat));
    if (bench) setBench(convert(bench));
    if (deadlift) setDeadlift(convert(deadlift));
    // running volume km ↔ mi
    const vol = parseFloat(weeklyVol);
    if (!isNaN(vol)) {
      setWeeklyVol(toImperial ? (vol * 0.621371).toFixed(0) : (vol / 0.621371).toFixed(0));
    }
  }

  async function finish() {
    if (seeding) return;
    setSeeding(true);
    try {
      const req: Record<string, unknown> = { goal };

      const displayName = `${firstName.trim()} ${lastName.trim()}`.trim();
      if (displayName) req.display_name = displayName;

      if (isRunningGoal(goal) && t300 && t15) {
        // Best-effort: compute metrics and cache them for the Field Test screen.
        try {
          const r = await computeMetrics({ age: 28, sex, time_300m: t300, time_1p5mi: t15 });
          actions.ftCompute(r);
        } catch { /* non-fatal — user can run the field test later */ }
      }

      const parseWeight = (s: string) => {
        const n = parseFloat(s);
        if (isNaN(n) || n <= 0) return undefined;
        return imperial ? lbsToKg(n) : n;
      };

      if (bodyweight) req.bodyweight_kg = parseWeight(bodyweight);
      if (squat) req.squat_1rm_kg = parseWeight(squat);
      if (bench) req.bench_1rm_kg = parseWeight(bench);
      if (deadlift) req.deadlift_1rm_kg = parseWeight(deadlift);
      if (run5k) { const s = parseMMSS(run5k); if (s) req.run_5k_seconds = s; }

      await completeOnboarding(req);
    } finally {
      setSeeding(false);
      goOverview();
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
        {ob === 1 && (
          <div>
            <h1 className="m-0 text-[30px] font-bold leading-[1.1] tracking-[-0.025em] text-ink">Let's set up your profile</h1>
            <p className="m-0 mb-7 mt-3 text-[14px] font-medium leading-[1.5] text-mute">Just the basics. You can change any of this later.</p>
            <div className="grid grid-cols-2 gap-4">
              <Field label="First name"><input value={firstName} onChange={(e) => setFirstName(e.target.value)} placeholder="First name" className={inputCls} /></Field>
              <Field label="Last name"><input value={lastName} onChange={(e) => setLastName(e.target.value)} placeholder="Last name" className={inputCls} /></Field>
              <Field label="Date of birth"><input type="date" className={inputCls} style={{ colorScheme: "dark" }} /></Field>
              <div className="block"><span className={labelCls}>Sex</span><Seg options={["Female", "Male"]} value={sex} onChange={(v) => actions.setSetting("sex", v)} /></div>
            </div>
            <div className="mt-[30px] flex justify-end"><button onClick={actions.obNext} className={btnPrimary}>Continue →</button></div>
          </div>
        )}

        {/* ── Step 2: training context ── */}
        {ob === 2 && (
          <div>
            <h1 className="m-0 text-[30px] font-bold leading-[1.1] tracking-[-0.025em] text-ink">Training context</h1>
            <p className="m-0 mb-7 mt-3 text-[14px] font-medium leading-[1.5] text-mute">So the plan speaks your language.</p>
            <div className="flex flex-col gap-4">
              <Field label="Training goal">
                <select value={goal} onChange={(e) => actions.setSetting("goal", e.target.value)} className={inputCls} style={{ colorScheme: "dark" }}>
                  {TRAINING_GOALS.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
                </select>
              </Field>
              <div className="block"><span className={labelCls}>Units</span>
                <Seg options={["Metric (km)", "Imperial (mi)"]} value={units} onChange={handleUnitsChange} />
              </div>
              {isRunningGoal(goal) ? (
                <Field label={`Current weekly volume (${imperial ? "mi" : "km"})`}>
                  <input value={weeklyVol} onChange={(e) => setWeeklyVol(e.target.value)} inputMode="decimal" className={inputCls} />
                </Field>
              ) : isStrengthGoal(goal) ? (
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Training days / week">
                    <input value={daysPerWeek} onChange={(e) => setDaysPerWeek(e.target.value)} inputMode="numeric" className={inputCls} />
                  </Field>
                  <Field label="Session duration (min)">
                    <input value={sessionDur} onChange={(e) => setSessionDur(e.target.value)} inputMode="numeric" className={inputCls} />
                  </Field>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Training days / week">
                    <input value={daysPerWeek} onChange={(e) => setDaysPerWeek(e.target.value)} inputMode="numeric" className={inputCls} />
                  </Field>
                  <Field label="Session duration (min)">
                    <input value={sessionDur} onChange={(e) => setSessionDur(e.target.value)} inputMode="numeric" className={inputCls} />
                  </Field>
                </div>
              )}
            </div>
            <div className="mt-[30px] flex justify-between">
              <button onClick={actions.obBack} className={btnBack}>← Back</button>
              <button onClick={actions.obNext} className={btnPrimary}>Continue →</button>
            </div>
          </div>
        )}

        {/* ── Step 3: seed your twin ── */}
        {ob === 3 && (
          <div>
            <h1 className="m-0 text-[30px] font-bold leading-[1.1] tracking-[-0.025em] text-ink">Seed your twin</h1>
            <p className="m-0 mb-7 mt-3 text-[14px] font-medium leading-[1.5] text-mute">
              {isRunningGoal(goal)
                ? "Enter a recent field test to initialize S(t) — or skip and run one from the Field Test screen."
                : isStrengthGoal(goal)
                  ? "Enter your current lifts to seed the strength model — or skip and add after your first session."
                  : "Enter any baseline data you have — all fields are optional."}
            </p>

            {isRunningGoal(goal) && (
              <div className="grid grid-cols-2 gap-4">
                <Field label="300 m time"><input value={t300} onChange={(e) => setT300(e.target.value)} placeholder="M:SS" className={`${inputCls} font-mono`} /></Field>
                <Field label="1.5 mi time"><input value={t15} onChange={(e) => setT15(e.target.value)} placeholder="MM:SS" className={`${inputCls} font-mono`} /></Field>
              </div>
            )}

            {isStrengthGoal(goal) && (
              <div className="grid grid-cols-2 gap-4">
                <Field label={`Bodyweight (${wUnit})`}><input value={bodyweight} onChange={(e) => setBodyweight(e.target.value)} inputMode="decimal" placeholder="—" className={`${inputCls} font-mono`} /></Field>
                <Field label={`Squat 1RM (${wUnit})`}><input value={squat} onChange={(e) => setSquat(e.target.value)} inputMode="decimal" placeholder="—" className={`${inputCls} font-mono`} /></Field>
                <Field label={`Bench 1RM (${wUnit})`}><input value={bench} onChange={(e) => setBench(e.target.value)} inputMode="decimal" placeholder="—" className={`${inputCls} font-mono`} /></Field>
                <Field label={`Deadlift 1RM (${wUnit})`}><input value={deadlift} onChange={(e) => setDeadlift(e.target.value)} inputMode="decimal" placeholder="—" className={`${inputCls} font-mono`} /></Field>
              </div>
            )}

            {!isRunningGoal(goal) && !isStrengthGoal(goal) && (
              <div className="grid grid-cols-2 gap-4">
                <Field label={`Bodyweight (${wUnit})`}><input value={bodyweight} onChange={(e) => setBodyweight(e.target.value)} inputMode="decimal" placeholder="—" className={`${inputCls} font-mono`} /></Field>
                <Field label="5K time (MM:SS)"><input value={run5k} onChange={(e) => setRun5k(e.target.value)} placeholder="—" className={`${inputCls} font-mono`} /></Field>
              </div>
            )}

            <GoalAnchorCard goal={goal} />

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
