// src/perflab/screens/OverviewScreen.tsx
import { useEffect, type ReactNode } from "react";
import { usePerfLab, DEFAULT_GOAL } from "../store";
import { Card, ReadinessRing, SectionLabel, SyncChip, Track } from "../ui";
import { buildCheckin, COLORS, DAYS, readinessColor, readinessNote, readinessWord } from "../sim";
import { useAuthedResource } from "../useAuthedResource";
import { useAuth } from "@/auth/useAuth";
import {
  getDashboardOverview,
  getReadiness,
  getStateHistory,
  getTodayPlannedSession,
  listMacrocycles,
  listObjectives,
  listWellness,
  listWorkouts,
} from "@/api/perfLabClient";
import { sortObjectives } from "../objectives";
import { activeMacrocycle, weekProgressLabel } from "../macrocycles";
import type {
  MacrocycleRead,
  ObjectiveRead,
  OverviewMetrics,
  PrescriptionExplanation,
  ReadinessScore,
  TodaySessionResponse,
  TrainingLoadMetrics,
  UnifiedStateVector,
  WellnessSampleOut,
  WorkoutLogSummary,
} from "@/types";

// Session-scoped guard: the daily check-in prompt auto-opens at most once per
// page load (survives Overview remounts, resets on a full reload). A module-level
// flag is intentional — it must not reopen after the athlete dismisses the modal.
let checkinPromptShown = false;

function StatCol({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">{label}</div>
      <div className="mt-2 font-mono text-[18px] font-semibold leading-none text-ink">{children}</div>
    </div>
  );
}

// ---- Real-data helpers (local; keep OverviewScreen's only export a component) ----

const todayIso = () => new Date().toISOString().slice(0, 10);

/** Time-of-day greeting prefix from the wall clock. */
function greetingPrefix(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

/** "Saturday · 4 Jul" from the current date. */
function dateLine(): string {
  const d = new Date();
  const weekday = d.toLocaleDateString(undefined, { weekday: "long" });
  const month = d.toLocaleDateString(undefined, { month: "short" });
  return `${weekday} · ${d.getDate()} ${month}`;
}

/** Compact "Xh ago" from an ISO timestamp, for the sync chip. */
function relativeTime(iso: string): string {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** Mean of the six fatigue axes (0–100), from the decomposed vector when present,
 *  else the legacy fatigue scalars. */
function meanFatigue(sv: UnifiedStateVector): number {
  const f = sv.fatigue_f;
  const vals = f
    ? [f.cns, f.muscular, f.metabolic, f.structural, f.tendon, f.grip]
    : [sv.f_nm_central, sv.f_nm_peripheral, sv.f_met_systemic, sv.f_struct_damage];
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

/** Per-day readiness proxy ~ (1 − mean fatigue), scaled 0–100 — mirrors the
 *  backend `overall_readiness` intent for the trend line. */
function stateReadinessProxy(sv: UnifiedStateVector): number {
  return Math.round(Math.max(0, Math.min(100, 100 - meanFatigue(sv))));
}

/** Highest-loaded tissue region + its value, from the latest state vector. */
function peakTissue(sv: UnifiedStateVector): { region: string; value: number } | null {
  const t = sv.tissue_t;
  if (!t) return null;
  const entries = Object.entries(t) as [string, number][];
  const [region, value] = entries.sort((a, b) => b[1] - a[1])[0];
  return { region: region.charAt(0).toUpperCase() + region.slice(1), value: Math.round(value) };
}

const SORE_WORDS = ["None", "Mild", "Moderate", "High"];
/** Map the backend 0–10 soreness scalar (higher = worse) to a word. */
function sorenessWord(v: number): string {
  if (v <= 1) return SORE_WORDS[0];
  if (v <= 4) return SORE_WORDS[1];
  if (v <= 7) return SORE_WORDS[2];
  return SORE_WORDS[3];
}

// ---- Training-load (ACWR) tile ----
// The sweet-spot bar's horizontal axis runs ACWR 0 → 2; a value maps linearly to
// its % position (clamped), so 0.8 → 40%, 1.3 → 65%, 1.08 → 54%.
const ACWR_AXIS_MAX = 2;
const acwrPct = (v: number) => Math.max(0, Math.min(100, (v / ACWR_AXIS_MAX) * 100));

// Status → user-facing word + text color. "insufficient" (new users, no baseline)
// reads as "building baseline" rather than an invented judgement.
const LOAD_STATUS: Record<TrainingLoadMetrics["status"], { label: string; color: string }> = {
  optimal: { label: "optimal", color: "text-good" },
  low: { label: "low", color: "text-warn" },
  high: { label: "high", color: "text-hot" },
  insufficient: { label: "building baseline", color: "text-faint" },
};

/** "5-day streak" with a graceful zero case (never a fabricated streak). */
function streakLabel(days: number): string {
  if (days <= 0) return "No streak yet";
  return `${days}-day streak`;
}

const SIGNAL_LABELS: Record<string, string> = {
  hrv: "HRV",
  hrv_ms: "HRV",
  sleep: "Sleep",
  sleep_hours: "Sleep",
  sleep_quality: "Sleep quality",
  resting_hr: "Resting HR",
  rhr: "Resting HR",
  soreness: "Soreness",
  mood: "Motivation",
};
const signalLabel = (s: string) => SIGNAL_LABELS[s] ?? s.replace(/_/g, " ");

/** The loosely-typed prescription dict off TodaySessionResponse (a serialized
 *  WorkoutPrescription). We read only the fields we render. */
type PrescDict = {
  focus?: string;
  rationale?: string;
  type?: string;
  duration_min?: number;
  exercises?: unknown[];
  why?: PrescriptionExplanation | null;
};

// Overview hero: the athlete's top objective (active-first, highest priority,
// nearest deadline — same ordering as ObjectivesScreen), fetched live and
// re-fetched when the objectives list changes. Replaces the old frontend-only
// "Goal race" / Valencia mock. Signed-out, empty, or still-loading all fall
// back to a CTA into the Objectives screen. Clicking navigates there.
function GoalObjectiveCard() {
  const { state, actions } = usePerfLab();
  const { data, loading } = useAuthedResource<ObjectiveRead[]>(
    (t) => listObjectives(t),
    [state.objectivesRefreshKey],
  );
  const top = data && data.length ? sortObjectives(data)[0] : null;
  const gradient = { background: "radial-gradient(120% 140% at 100% 0%,#11321f,#111419 55%)" };

  if (!top) {
    return (
      <Card onClick={() => actions.setScreen("objectives")} className="flex items-center justify-between gap-4" style={gradient}>
        <div>
          <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-ac">Objective</div>
          <div className="mt-[10px] text-[19px] font-bold leading-none tracking-[-0.01em] text-ink">
            {loading ? "Loading your objective…" : "Set a goal to aim at"}
          </div>
          <div className="mt-[9px] text-[12px] font-medium leading-none text-mute">
            {loading ? "Fetching what your plan is pointed at." : "A race, a meet, a Hyrox, a PR — give your plan a target."}
          </div>
        </div>
        <div className="flex-none text-right">
          <div className="font-mono text-[13px] font-semibold leading-none text-ac">Objectives →</div>
        </div>
      </Card>
    );
  }

  const pct = top.progress.pct;
  return (
    <Card onClick={() => actions.setScreen("objectives")} className="flex items-center justify-between gap-4" style={gradient}>
      <div>
        <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-ac">
          Objective{top.target_date ? ` · ${top.target_date}` : ""}
        </div>
        <div className="mt-[10px] text-[19px] font-bold leading-none tracking-[-0.01em] text-ink">{top.label}</div>
        <div className="mt-[9px] text-[12px] font-medium leading-none text-mute">
          {top.target_value != null ? (
            <>
              Target <span className="text-soft">{top.target_value}{top.target_unit ? ` ${top.target_unit}` : ""}</span>
              {pct != null && <> · <span className="text-teal">{Math.round(pct)}% there</span></>}
            </>
          ) : (
            "Countdown-only · link a benchmark for progress"
          )}
        </div>
      </div>
      <div className="flex-none text-right">
        {top.days_to_go != null ? (
          <>
            <div className="font-mono text-[42px] font-semibold leading-[0.9] tracking-[-0.02em] text-ink">{top.days_to_go}</div>
            <div className="mt-[5px] font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">days to go →</div>
          </>
        ) : (
          <div className="font-mono text-[13px] font-semibold leading-none text-ac">Objectives →</div>
        )}
      </div>
    </Card>
  );
}

// Real cross-block "week X of Y" for the header subtitle, from the active
// macrocycle's week_progress (GET /v1/macrocycles). Replaces the old hard-coded
// "Mid-base block, week 3 of 7" mock. Renders nothing (no clause) when signed
// out, still loading, or when there is no active program — never a fabricated
// week.
function ProgramWeek() {
  const { state } = usePerfLab();
  const { data } = useAuthedResource<MacrocycleRead[]>(
    (t) => listMacrocycles(t),
    [state.macrocyclesRefreshKey],
  );
  const macro = activeMacrocycle(data);
  if (!macro) return null;
  return <>&nbsp;·&nbsp; {weekProgressLabel(macro.week_progress)}</>;
}

const btnGhost = "rounded-[9px] border border-white/10 bg-white/[0.04] px-[14px] py-[9px] text-[12.5px] font-semibold leading-none text-soft";

// The "Recommended today" card — real planned session + prescription from
// GET /v1/planning/today. Signed-out / nothing-scheduled / loading all show a
// graceful state instead of the old "Tempo intervals · Zone 3" literals. The
// resource is fetched by OverviewScreen and passed in (its `why` also feeds Insights).
function RecommendedToday({ token, data, loading }: { token: string | null; data: TodaySessionResponse | null; loading: boolean }) {
  const { actions } = usePerfLab();

  const session = data?.session ?? null;
  const presc = (data?.prescription ?? null) as PrescDict | null;
  const hasContent = !!session || !!presc;

  if (!token || !hasContent) {
    const headline = !token
      ? "Sign in to see today's session"
      : loading
        ? "Loading today's session…"
        : "Nothing scheduled today";
    const sub = !token
      ? "Your recommended session is generated from your plan and current readiness."
      : loading
        ? "Fetching your plan and current readiness."
        : "Plan a block to get a recommended session, or take today as recovery.";
    return (
      <div className="flex-1">
        <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-ac">Recommended today</div>
        <div className="mt-[9px] text-[22px] font-bold leading-[1.1] text-ink">{headline}</div>
        <div className="mt-[9px] max-w-[380px] text-[13px] font-medium leading-[1.5] text-mute">{sub}</div>
        <div className="mt-4 flex gap-[10px]">
          <button onClick={() => actions.setScreen("planning")} className={btnGhost}>Plan your week</button>
        </div>
      </div>
    );
  }

  const title =
    session && session.category
      ? `${session.category}${session.modality ? ` · ${session.modality}` : ""}`
      : presc?.focus || session?.modality || "Today's session";
  const prose = presc?.rationale || presc?.focus || "";

  const stats: { label: string; value: ReactNode }[] = [];
  if (presc?.duration_min != null) stats.push({ label: "Duration", value: `~${Math.round(presc.duration_min)} min` });
  if (presc?.exercises && presc.exercises.length) stats.push({ label: "Exercises", value: presc.exercises.length });
  if (session?.week_number != null) {
    stats.push({
      label: "Week",
      value: (
        <>
          {session.week_number}
          {session.is_deload && <span className="ml-1 text-[11px] text-warn">deload</span>}
        </>
      ),
    });
  }
  const focus = presc?.type || presc?.focus || session?.category;
  if (focus && stats.length < 4) stats.push({ label: "Focus", value: focus });

  return (
    <div className="flex-1">
      <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-ac">Recommended today</div>
      <div className="mt-[9px] text-[22px] font-bold leading-[1.1] text-ink">{title}</div>
      {prose && <div className="mt-[9px] max-w-[380px] text-[13px] font-medium leading-[1.5] text-mute">{prose}</div>}
      <div className="mt-4 flex gap-[10px]">
        <button onClick={actions.openSession} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-4 py-[10px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Start session</button>
        <button onClick={() => actions.setScreen("planning")} className={btnGhost}>View week</button>
      </div>
      {stats.length > 0 && (
        <div className="mt-5 grid grid-cols-4 gap-[18px] border-t border-white/[0.06] pt-[18px]">
          {stats.map((s) => (
            <StatCol key={s.label} label={s.label}>{s.value}</StatCol>
          ))}
        </div>
      )}
      {presc?.why?.goal_alignment && (
        <div className="mt-4 flex items-start gap-[9px] rounded-[11px] border border-ac/[0.18] bg-ac/[0.05] px-[13px] py-[11px]">
          <span className="text-[12px] font-medium leading-[1.5] text-mute">{presc.why.goal_alignment}</span>
        </div>
      )}
    </div>
  );
}

export function OverviewScreen() {
  const { state, actions } = usePerfLab();
  const { token, user, profile, email, isGuest } = useAuth();
  const ci = state.checkin;

  // ---- Live backend resources (idle for guests → local/sim fallback) ----
  const readinessRes = useAuthedResource<ReadinessScore>((t) => getReadiness(t), [state.readinessRefreshKey]);
  const wellnessRes = useAuthedResource<WellnessSampleOut[]>((t) => listWellness(t, 1), [state.readinessRefreshKey]);
  const historyRes = useAuthedResource<UnifiedStateVector[]>((t) => getStateHistory(t, 14), [state.readinessRefreshKey]);
  const workoutsRes = useAuthedResource<WorkoutLogSummary[]>((t) => listWorkouts(t, 5), []);
  const overviewRes = useAuthedResource<OverviewMetrics>(getDashboardOverview, []);

  const goal = profile?.primary_goal || state.settings.goal || DEFAULT_GOAL;
  const todayRes = useAuthedResource<TodaySessionResponse>((t) => getTodayPlannedSession(goal, t), [goal]);

  // ---- Daily check-in prompt: auto-open once/session if no sample logged today ----
  const wellnessData = wellnessRes.data;
  const wellnessLoading = wellnessRes.loading;
  useEffect(() => {
    if (checkinPromptShown || !token || wellnessLoading || wellnessData == null) return;
    checkinPromptShown = true; // resolved for this session — decide once, never re-prompt
    const latest = wellnessData[0];
    if (!latest || latest.date !== todayIso()) actions.openCheckin();
  }, [token, wellnessLoading, wellnessData, actions]);

  // ---- Readiness (real when signed in; sim as guest / no-state fallback) ----
  const realReadiness = token ? readinessRes.data?.readiness ?? null : null;
  const simReady = buildCheckin(ci).readiness;
  const todayD = DAYS[DAYS.length - 1];
  const ovVal = realReadiness != null ? Math.round(realReadiness) : ci.done ? simReady : todayD.readiness;
  const ovColor = readinessColor(ovVal);
  const ovWord = readinessWord(ovVal);

  // ---- 14-day readiness trend (real proxy series; sim series for guests) ----
  const realSeries = token && historyRes.data && historyRes.data.length ? historyRes.data.map(stateReadinessProxy) : null;
  const series = realSeries ?? DAYS.slice(Math.max(0, DAYS.length - 14)).map((d) => d.readiness);
  const sN = series.length;
  const oW = 300, opad = 6, oTop = 8, oBot = 58;
  const ox = (i: number) => opad + (i / (sN - 1)) * (oW - 2 * opad);
  const oy = (r: number) => oBot - ((r - 20) / 80) * (oBot - oTop);
  const showSpark = sN >= 2;
  const ovLine = series.map((r, i) => `${ox(i).toFixed(1)},${oy(r).toFixed(1)}`).join(" ");
  let ovArea = `M ${ox(0).toFixed(1)} ${oBot}`;
  series.forEach((r, i) => (ovArea += ` L ${ox(i).toFixed(1)} ${oy(r).toFixed(1)}`));
  ovArea += ` L ${ox(sN - 1).toFixed(1)} ${oBot} Z`;
  const seriesLast = series[sN - 1];
  const ovDiff = Math.round(seriesLast - series[0]);
  const ovDelta = `${ovDiff >= 0 ? "+" : ""}${ovDiff} vs 2w ago`;

  // ---- "This morning" wellness tiles (latest real sample; ci fallback) ----
  const w = token ? wellnessRes.data?.[0] ?? null : null;
  const tiles: [string, ReactNode][] = [
    ["HRV", w?.hrv_ms != null ? `${w.hrv_ms} ms` : `${ci.hrv} ms`],
    ["Sleep", w?.sleep_hours != null ? `${w.sleep_hours} h` : `${ci.sleepH} h`],
    ["Rest HR", w?.resting_hr != null ? `${w.resting_hr} bpm` : `${ci.rhr} bpm`],
    ["Soreness", w?.soreness != null ? sorenessWord(w.soreness) : ci.soreness.charAt(0).toUpperCase() + ci.soreness.slice(1)],
  ];
  const loggedToday = w != null && w.date === todayIso();
  const morningNote = token
    ? loggedToday
      ? "Logged this morning"
      : "Tap to log this morning"
    : ci.done
      ? "Logged this morning"
      : "Tap to log this morning";

  // ---- Twin snapshot: latest real state vector (sim for guests) ----
  const latest = token ? historyRes.data?.[historyRes.data.length - 1] ?? null : null;
  const snapAerobic = latest ? Math.round(latest.capacity_x?.aerobic ?? latest.c_met_aerobic) : todayD.C.aerobic;
  const snapStrength = latest ? Math.round(latest.capacity_x?.max_strength ?? latest.c_nm_force) : todayD.C.strength;
  const snapMeanFat = latest ? Math.round(meanFatigue(latest)) : Math.round(Object.values(todayD.F).reduce((a, b) => a + b, 0) / 6);
  const simTissue = Object.entries(todayD.T).sort((a, b) => b[1] - a[1])[0];
  const snapTissue = latest ? peakTissue(latest) : { region: simTissue[0], value: simTissue[1] };

  // ---- Insights: derived from real readiness components + prescription why ----
  const insights: { dot: string; title: string; desc: string }[] = [];
  if (token) {
    const comps = [...(readinessRes.data?.components ?? [])].sort((a, b) => a.contribution - b.contribution);
    const worst = comps[0];
    const best = comps[comps.length - 1];
    if (worst && worst.contribution < -0.05) {
      insights.push({ dot: COLORS.hot, title: `${signalLabel(worst.signal)} below baseline`, desc: `${worst.value} vs baseline ${Math.round(worst.baseline)} — pulling readiness down today.` });
    }
    if (best && best !== worst && best.contribution > 0.05) {
      insights.push({ dot: COLORS.good, title: `${signalLabel(best.signal)} above baseline`, desc: `${best.value} vs baseline ${Math.round(best.baseline)} — supporting readiness today.` });
    }
    // Prescription rationale for today's recommended session.
    const why = ((todayRes.data?.prescription ?? null) as PrescDict | null)?.why ?? null;
    (why?.constraints_applied ?? []).forEach((c) => insights.push({ dot: COLORS.warn, title: "Constraint applied", desc: c }));
    (why?.warnings ?? []).forEach((wn) => insights.push({ dot: COLORS.hot, title: "Heads up", desc: wn }));
  } else {
    // Guest: a single honest insight from the local sim, not fabricated metrics.
    insights.push({ dot: readinessColor(ovVal), title: `Readiness ${ovVal} · ${ovWord.toLowerCase()}`, desc: readinessNote(ovVal) });
  }

  // ---- Sync chip: real "last synced" from newest wellness / state timestamp ----
  const lastSyncIso = w?.created_at ?? (latest?.timestamp ?? null);

  // ---- Training-load + habit tiles (GET /v1/dashboard/overview) ----
  // Guests / loading → null resource → neutral "—" placeholders (never fabricated).
  const load = overviewRes.data?.training_load ?? null;
  const adherence = overviewRes.data?.adherence ?? null;
  const loadStatus = LOAD_STATUS[load?.status ?? "insufficient"];
  const acwrText = load?.acwr != null ? load.acwr.toFixed(2) : "—";
  const sweetLow = load?.sweet_spot_low ?? 0.8;
  const sweetHigh = load?.sweet_spot_high ?? 1.3;
  const adherenceText = adherence?.pct != null ? `${Math.round(adherence.pct)}%` : "—";

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <header className="flex items-start justify-between gap-5">
        <div>
          <h1 className="m-0 text-[25px] font-bold leading-none tracking-[-0.02em] text-ink">
            {greetingPrefix()}, {profile?.display_name || (user?.email ?? email).split("@")[0] || (isGuest ? "Guest" : "Athlete")}
          </h1>
          <p className="m-0 mt-[9px] text-[13.5px] font-medium leading-[1.5] text-[#7c818c]">{dateLine()}<ProgramWeek /></p>
        </div>
        <div className="flex items-center gap-[9px]">
          {lastSyncIso && <SyncChip label={`Synced ${relativeTime(lastSyncIso)}`} />}
          <button onClick={actions.openCheckin} className={btnGhost}>Check in</button>
          <button onClick={actions.openLog} className="rounded-[9px] bg-ink px-[15px] py-[9px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Log workout</button>
        </div>
      </header>

      {/* Top objective + this-morning */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.3fr_1fr]">
        <GoalObjectiveCard />

        <Card className="flex flex-col gap-[14px]">
          <div className="flex items-center justify-between">
            <SectionLabel>This morning</SectionLabel>
            <div className="flex items-center gap-[9px]">
              <span className="font-mono text-[13px] font-semibold leading-none" style={{ color: ovColor }}>{ovVal} {ovWord}</span>
              <button onClick={actions.openCheckin} className="rounded-[8px] bg-ac px-[11px] py-[7px] text-[11px] font-semibold leading-none text-[#0a0c10]">Check in →</button>
            </div>
          </div>
          <div className="grid grid-cols-4 gap-3">
            {tiles.map(([l, v]) => (
              <div key={l}>
                <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.1em] text-faint">{l}</div>
                <div className="mt-[6px] font-mono text-[15px] font-semibold leading-none text-ink">{v}</div>
              </div>
            ))}
          </div>
          <div className="text-[10.5px] font-medium leading-none text-dim">{morningNote} · feeds readiness &amp; twin</div>
        </Card>
      </div>

      {/* Today + right stack */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
        <Card className="p-6">
          <div className="flex items-start gap-6">
            <div className="w-[300px] flex-none">
              <div className="flex items-center gap-4">
                <ReadinessRing value={ovVal} color={ovColor} size={96} inner={74} valueClassName="text-[29px]" />
                <div>
                  <SectionLabel className="text-faint">Readiness</SectionLabel>
                  <div className="mt-2 text-[17px] font-bold leading-none" style={{ color: ovColor }}>{ovWord}</div>
                  {showSpark && <div className="mt-[7px] text-[11px] font-medium leading-none text-good">{ovDelta}</div>}
                </div>
              </div>
              <div className="mt-4 font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.14em] text-dim">Last 14 days</div>
              {showSpark ? (
                <svg viewBox="0 0 300 70" preserveAspectRatio="none" className="mt-2 block h-[56px] w-full overflow-visible">
                  <defs>
                    <linearGradient id="ovg" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0" stopColor="rgba(198,241,53,.26)" />
                      <stop offset="1" stopColor="rgba(198,241,53,0)" />
                    </linearGradient>
                  </defs>
                  <path d={ovArea} fill="url(#ovg)" />
                  <polyline points={ovLine} fill="none" stroke="var(--ac)" strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
                  <circle cx={ox(sN - 1)} cy={oy(seriesLast)} r="3.5" fill="var(--ac)" />
                </svg>
              ) : (
                <div className="mt-2 flex h-[56px] items-center text-[11px] font-medium text-dim">Not enough history yet — trend appears after a few days.</div>
              )}
            </div>
            <RecommendedToday token={token} data={todayRes.data} loading={todayRes.loading} />
          </div>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <div className="flex items-center justify-between">
              <SectionLabel className="text-faint">Training load</SectionLabel>
              <span className={`font-mono text-[11px] font-semibold leading-none ${loadStatus.color}`}>{loadStatus.label}</span>
            </div>
            <div className="mt-3 flex items-end gap-2">
              <span className="font-mono text-[30px] font-semibold leading-none text-ink">{acwrText}</span>
              <span className="mb-1 text-[11px] font-medium leading-none text-faint">ACWR · 7d/28d</span>
            </div>
            <div className="relative mt-3 h-[6px] overflow-hidden rounded-full bg-white/[0.07]">
              <div className="absolute bottom-0 top-0 bg-good/25" style={{ left: `${acwrPct(sweetLow)}%`, right: `${100 - acwrPct(sweetHigh)}%` }} />
              {load?.acwr != null && (
                <div className="absolute top-[-2px] h-[10px] w-[3px] rounded-[2px] bg-ac" style={{ left: `${acwrPct(load.acwr)}%` }} />
              )}
            </div>
            <div className="mt-[7px] font-mono text-[10px] leading-none text-dim">sweet spot {sweetLow}–{sweetHigh}</div>
          </Card>
          <Card>
            <div className="flex items-center justify-between">
              <SectionLabel className="text-faint">Habit</SectionLabel>
              <span className="font-mono text-[11px] font-semibold leading-none text-ac">{streakLabel(adherence?.streak_days ?? 0)}</span>
            </div>
            <div className="mt-3 flex items-end gap-2">
              <span className="font-mono text-[30px] font-semibold leading-none text-ink">{adherenceText}</span>
              <span className="mb-1 text-[11px] font-medium leading-none text-faint">adherence</span>
            </div>
            <Track pct={adherence?.pct ?? 0} className="mt-3 h-[6px]" />
          </Card>
        </div>
      </div>

      {/* Recent + twin snapshot */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <SectionLabel className="mb-4">Recent activity</SectionLabel>
          <RecentActivity token={token} workouts={workoutsRes.data} loading={workoutsRes.loading} />
        </Card>
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <SectionLabel>Twin snapshot</SectionLabel>
            <button onClick={() => actions.setScreen("twin")} className="text-[11px] font-medium leading-none text-teal">Open twin →</button>
          </div>
          <div className="grid grid-cols-2 gap-x-[22px] gap-y-[14px]">
            <Snap label="Aerobic" value={snapAerobic} />
            <Snap label="Strength" value={snapStrength} color="text-teal" />
            <Snap label="Mean fatigue" value={snapMeanFat} color="text-warn" />
            <Snap
              label="Peak tissue"
              value={snapTissue ? <>{snapTissue.value} <span className="text-[11px] text-faint">{snapTissue.region.toLowerCase()}</span></> : "—"}
              color="text-warn"
            />
          </div>
        </Card>
      </div>

      <Card>
        <div className="mb-[14px] flex items-center justify-between">
          <SectionLabel>Insights</SectionLabel>
          <span className="text-[11px] font-medium leading-none text-dim">from readiness &amp; your plan</span>
        </div>
        {insights.length === 0 ? (
          <div className="py-[11px] text-[12px] font-medium leading-[1.5] text-dim">No insights yet — log a check-in or a workout and they'll appear here.</div>
        ) : (
          <div className="flex flex-col gap-[2px]">
            {insights.map((a, i) => (
              <div key={i} className="flex items-start gap-3 border-b border-white/[0.05] py-[11px] last:border-0">
                <span className="mt-[3px] h-[9px] w-[9px] flex-none rounded-full" style={{ background: a.dot }} />
                <div>
                  <div className="text-[13px] font-semibold leading-none text-[#e6e8ec]">{a.title}</div>
                  <div className="mt-1 text-[11.5px] font-medium leading-[1.5] text-[#7c818c]">{a.desc}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </section>
  );
}

// Recent activity from GET /v1/workouts (most recent first). Empty / signed-out
// / loading states replace the old three hard-coded rows.
function RecentActivity({ token, workouts, loading }: { token: string | null; workouts: WorkoutLogSummary[] | null; loading: boolean }) {
  if (!token) {
    return <div className="py-[11px] text-[12px] font-medium leading-[1.5] text-dim">Sign in to see your logged sessions.</div>;
  }
  if (loading && !workouts) {
    return <div className="py-[11px] text-[12px] font-medium leading-[1.5] text-dim">Loading recent sessions…</div>;
  }
  if (!workouts || workouts.length === 0) {
    return <div className="py-[11px] text-[12px] font-medium leading-[1.5] text-dim">No workouts logged yet — log one to see it here.</div>;
  }
  const dot = (rpe: number) => (rpe >= 8 ? COLORS.hot : rpe >= 6 ? COLORS.warn : COLORS.good);
  return (
    <div className="flex flex-col gap-[2px]">
      {workouts.map((wk) => {
        const km = wk.distance_meters ? `${(wk.distance_meters / 1000).toFixed(1)} km` : null;
        const when = new Date(wk.logged_at).toLocaleDateString(undefined, { weekday: "short" });
        const sub = [when, km, `RPE ${wk.session_rpe}`].filter(Boolean).join(" · ");
        return (
          <div key={wk.id} className="flex items-center gap-[13px] border-b border-white/[0.05] py-[11px] last:border-0">
            <div className="h-[9px] w-[9px] flex-none rounded-full" style={{ background: dot(wk.session_rpe) }} />
            <div className="flex-1">
              <div className="text-[13px] font-semibold leading-none text-[#e6e8ec]">
                {wk.modality.charAt(0).toUpperCase() + wk.modality.slice(1)} · {Math.round(wk.duration_minutes)} min
              </div>
              <div className="mt-1 text-[11px] font-medium leading-none text-faint">{sub}</div>
            </div>
            <span className="font-mono text-[11px] font-semibold leading-none" style={{ color: dot(wk.session_rpe) }}>load {Math.round(wk.total_volume_load)}</span>
          </div>
        );
      })}
    </div>
  );
}

function Snap({ label, value, color = "text-ink" }: { label: string; value: ReactNode; color?: string }) {
  return (
    <div>
      <div className="text-[11px] font-medium leading-none text-mute">{label}</div>
      <div className={`mt-[5px] font-mono text-[22px] font-semibold leading-none ${color}`}>{value}</div>
    </div>
  );
}
