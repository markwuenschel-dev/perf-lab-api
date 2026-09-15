// src/perflab/screens/overview/AuthedOverview.tsx
//
// The AUTHENTICATED Overview. Every number on this screen is one the backend
// returned, or an explicit empty/unavailable state (map #182, L1).
//
// This file MUST NOT import sim.ts, any guest-preview module, or any fixture
// store, and it must not compute an athlete measurement. That is not a convention
// — `overviewBoundary.test.ts` walks this module's transitive import graph with
// the TypeScript compiler API and fails the build if a forbidden module is
// reachable, and `overviewModel.test.ts` fails if any authenticated state produces
// a fabricated value. Both are blocking.
//
// Interpretation lives in ./overviewModel, not here. The job of this file is to
// turn `MetricState` values into JSX and nothing else — no `??` fallbacks, no
// ternaries that substitute one metric for another.
import * as api from "@/api/perfLabClient";
import type {
  MacrocycleRead,
  ObjectiveRead,
  OverviewMetrics,
  ReadinessScore,
  StateHistorySnapshotRead,
  TodaySessionResponse,
  TrainingLoadMetrics,
  WellnessSampleOut,
  WorkoutLogSummary,
} from "@/types";
import type { ReactNode } from "react";
import { useAuth } from "@/auth/useAuth";
import { activeMacrocycle, weekProgressLabel } from "../../macrocycles";
import { sortObjectives } from "../../objectives";
import { COLORS, readinessColor, readinessWord } from "../../readinessPresentation";
import { resourceData, type AuthedResource } from "../../resource";
import { relativeTime } from "../../stateVector";
import { usePerfLab } from "../../store";
import { Card, ReadinessRing, SectionLabel, SyncChip, Track } from "../../ui";
import { useAuthedResource } from "../../useAuthedResource";
import { Gauge, Sparkline } from "../../viz";
import {
  habitSection,
  loadSection,
  morningSection,
  primaryActionSection,
  readinessSection,
  trendSection,
  twinSnapshotSection,
  type MetricState,
} from "./overviewModel";
import { EmptyLine, MetricText, NeutralRing, OverviewHeader, Snap, StatCol } from "./overviewLeaves";

const btnGhost =
  "rounded-[9px] border border-white/10 bg-white/[0.04] px-[14px] py-[9px] text-[12.5px] font-semibold leading-none text-soft";

// Status → user-facing word + colour. "insufficient" (no baseline yet) reads as
// "building baseline" rather than an invented judgement.
const LOAD_STATUS: Record<TrainingLoadMetrics["status"], { label: string; color: string }> = {
  optimal: { label: "optimal", color: "text-good" },
  low: { label: "low", color: "text-warn" },
  high: { label: "high", color: "text-hot" },
  insufficient: { label: "building baseline", color: "text-faint" },
};

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

// The hand-rolled local prescription shape that used to live here is gone.
// `/v1/planning/today` now declares `prescription` as `WorkoutPrescription | null`
// in openapi.json, so `TodaySessionResponse["prescription"]` in types.gen.ts is the
// generated model and the casts that used to sit on it became unnecessary. Do not
// reintroduce a local type for this field — a hand-written duplicate cannot
// drift-check against the contract, which is exactly how the old one went stale.

export function AuthedOverview() {
  const { state, actions } = usePerfLab();
  const { user, profile, email } = useAuth();

  const readinessRes = useAuthedResource<ReadinessScore>((t) => api.getReadiness(t), [state.readinessRefreshKey]);
  // A day is several rows, not one: `WellnessSample` is keyed (user, date, source), so a
  // device sync and a manual check-in both land. Asking for 1 returned whichever was
  // ingested last and silently dropped the other's signals. `morningSection` folds the
  // latest date's rows per signal; the limit only has to be wide enough to contain them.
  const wellnessRes = useAuthedResource<WellnessSampleOut[]>((t) => api.listWellness(t, 8), [state.readinessRefreshKey]);
  // Annotated as the type the endpoint actually returns (#187). The selectors read
  // no snapshot_id and no confidence field, so behaviour is identical without them.
  const historyRes = useAuthedResource<StateHistorySnapshotRead[]>(
    (t) => api.getStateHistory(t, 14),
    [state.readinessRefreshKey],
  );
  const workoutsRes = useAuthedResource<WorkoutLogSummary[]>((t) => api.listWorkouts(t, 5), []);
  const overviewRes = useAuthedResource<OverviewMetrics>((t) => api.getDashboardOverview(t), []);
  const goal = state.settings.goal;
  const todayRes = useAuthedResource<TodaySessionResponse>((t) => api.getTodayPlannedSession(goal, t), [goal]);

  // There is no check-in auto-prompt here, and its absence is deliberate.
  // L4 requires the prompt be scoped to a canonical athlete day; #191 made that day
  // backend-owned and ruled that when day context is missing the client must NOT
  // derive one locally and must NOT prompt. No such endpoint exists yet (verified:
  // no AthleteDayContext, no day_key, no athlete timezone column anywhere), so the
  // only compliant behaviour is the manual "Check in" button in the header.

  const readiness = readinessSection(readinessRes);
  const trend = trendSection(historyRes);
  const morning = morningSection(wellnessRes, readinessRes);
  const twin = twinSnapshotSection(historyRes);
  const load = loadSection(overviewRes);
  const habit = habitSection(overviewRes);
  const primaryAction = primaryActionSection();

  const history = resourceData(historyRes);
  const newest = history && history.length ? history[history.length - 1] : null;

  return (
    <>
      <OverviewHeader
        name={profile?.display_name || (user?.email ?? email).split("@")[0] || "Athlete"}
        subtitleExtra={<ProgramWeek />}
        actions={
          <>
            {newest && <SyncChip label={`Synced ${relativeTime(newest.timestamp)}`} />}
            <button onClick={actions.openCheckin} className={btnGhost}>Check in</button>
            <button onClick={actions.openLog} className="rounded-[9px] bg-ink px-[15px] py-[9px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Log workout</button>
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.3fr_1fr]">
        <GoalObjectiveCard />
        <MorningCard morning={morning} onCheckin={actions.openCheckin} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
        <Card className="p-6">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-start">
            <div className="w-full sm:w-[300px] sm:flex-none">
              <ReadinessBlock readiness={readiness} />
              <TrendBlock trend={trend} />
            </div>
            <RecommendedToday resource={todayRes} primaryAction={primaryAction} onPlan={() => actions.setScreen("planning")} />
          </div>
        </Card>

        <div className="flex flex-col gap-4">
          <TrainingLoadCard load={load} />
          <HabitCard habit={habit} />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <SectionLabel className="mb-4">Recent activity</SectionLabel>
          <RecentActivity resource={workoutsRes} />
        </Card>
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <SectionLabel>Twin snapshot</SectionLabel>
            <button onClick={() => actions.setScreen("twin")} className="text-[11px] font-medium leading-none text-teal">Open twin →</button>
          </div>
          <div className="grid grid-cols-2 gap-x-[22px] gap-y-[14px]">
            <Snap label="Aerobic" value={<MetricText state={twin.aerobic} />} />
            <Snap label="Strength" value={<MetricText state={twin.strength} />} color="text-teal" />
            <Snap label="Mean fatigue" value={<MetricText state={twin.meanFatigue} />} color="text-warn" />
            <Snap
              label="Peak tissue"
              value={
                twin.peakTissue.kind === "value" ? (
                  <>
                    {twin.peakTissue.value.value}{" "}
                    <span className="text-[11px] text-faint">{twin.peakTissue.value.region.toLowerCase()}</span>
                  </>
                ) : (
                  <MetricText state={{ kind: twin.peakTissue.kind } as MetricState<string>} />
                )
              }
              color="text-warn"
            />
          </div>
          {twin.aerobic.kind === "empty" && (
            <EmptyLine>No twin state yet — log a workout or run a field test to seed it.</EmptyLine>
          )}
        </Card>
      </div>

      <InsightsCard readinessRes={readinessRes} todayRes={todayRes} />
    </>
  );
}

// ---- Readiness ------------------------------------------------------------------

function ReadinessBlock({ readiness }: { readiness: ReturnType<typeof readinessSection> }) {
  const { score, confidenceBand } = readiness;

  if (score.kind !== "value") {
    const message =
      score.kind === "loading"
        ? "Loading your readiness…"
        : score.kind === "empty"
          ? "Readiness isn't available yet — check in or log a workout."
          : score.reason === "not_authenticated"
            ? "Readiness is unavailable."
            : "Couldn't load your readiness. Reload to try again.";
    return (
      <div className="flex items-center gap-4">
        <NeutralRing />
        <div>
          <SectionLabel className="text-faint">Readiness</SectionLabel>
          <div className="mt-2 max-w-[180px] text-[12.5px] font-medium leading-[1.5] text-mute">{message}</div>
        </div>
      </div>
    );
  }

  const color = readinessColor(score.value);
  return (
    <div className="flex items-center gap-4">
      <ReadinessRing value={score.value} color={color} size={96} inner={74} valueClassName="text-[29px]" />
      <div>
        <SectionLabel className="text-faint">Readiness</SectionLabel>
        <div className="mt-2 text-[17px] font-bold leading-none" style={{ color }}>{readinessWord(score.value)}</div>
        {confidenceBand.kind === "value" && (
          <div className="mt-[7px] text-[10.5px] font-semibold leading-none text-dim">
            Confidence: <span className="text-mute">{confidenceBand.value}</span>
            {confidenceBand.value === "low" && <span className="ml-1 text-warn">· limited data — check in to improve</span>}
          </div>
        )}
      </div>
    </div>
  );
}

// ---- Fatigue-derived trend (#186) -----------------------------------------------

function TrendBlock({ trend }: { trend: ReturnType<typeof trendSection> }) {
  // Rendered as its own block BELOW the readiness group, with its own label and a
  // separating rule. #186: the delta used to sit between the readiness word and the
  // backend confidence band while being computed from the fatigue proxy, so it read
  // as describing the ring above it. It does not.
  return (
    <div className="mt-5 border-t border-white/[0.06] pt-4">
      <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.14em] text-dim">
        {trend.label} · last 14 days
      </div>
      {trend.series.kind === "value" ? (
        <>
          {trend.delta.kind === "value" && (
            <div className="mt-[7px] text-[11px] font-medium leading-none text-good">
              {trend.delta.value >= 0 ? "+" : ""}{trend.delta.value} vs 2w ago
            </div>
          )}
          <Sparkline values={trend.series.value} min={20} max={100} width={300} height={70} className="mt-2 block h-[56px] w-full" />
        </>
      ) : (
        <div className="mt-2 flex h-[56px] items-center text-[11px] font-medium text-dim">
          {trend.series.kind === "loading"
            ? "Loading your trend…"
            : trend.series.kind === "empty"
              ? trend.series.reason === "no_data"
                ? "No training history yet — your trend appears once state is recorded."
                : "Not enough history yet — trend appears after a few days."
              : "Couldn't load your history."}
        </div>
      )}
    </div>
  );
}

// ---- This morning ----------------------------------------------------------------

function MorningCard({
  morning,
  onCheckin,
}: {
  morning: ReturnType<typeof morningSection>;
  onCheckin: () => void;
}) {
  return (
    <Card className="flex flex-col gap-[14px]">
      <div className="flex items-center justify-between">
        <SectionLabel>This morning</SectionLabel>
        <button onClick={onCheckin} className="rounded-[8px] bg-ac px-[11px] py-[7px] text-[11px] font-semibold leading-none text-[#0a0c10]">Check in →</button>
      </div>
      <div className="grid grid-cols-4 gap-3">
        {morning.metrics.map((m) => (
          <div key={m.key}>
            <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.1em] text-faint">{m.label}</div>
            <div className="mt-[6px] font-mono text-[15px] font-semibold leading-none text-ink">
              <MetricText state={m.value} />
            </div>
          </div>
        ))}
      </div>
      <div className="text-[10.5px] font-medium leading-none text-dim">
        {morning.lastLoggedDate.kind === "value"
          ? `Last check-in ${morning.lastLoggedDate.value} · feeds readiness & twin`
          : morning.lastLoggedDate.kind === "loading"
            ? "Loading your latest check-in…"
            : morning.lastLoggedDate.kind === "empty"
              ? "No check-in logged yet · feeds readiness & twin"
              : "Couldn't load your check-ins."}
      </div>
    </Card>
  );
}

// ---- Recommended today -----------------------------------------------------------

function RecommendedToday({
  resource,
  primaryAction,
  onPlan,
}: {
  resource: AuthedResource<TodaySessionResponse>;
  primaryAction: ReturnType<typeof primaryActionSection>;
  onPlan: () => void;
}) {
  const data = resourceData(resource);
  const session = data ? data.session : null;
  const presc = (data ? data.prescription : null) ?? null;
  const hasContent = !!session || !!presc;

  if (!hasContent) {
    const headline =
      resource.status === "loading" ? "Loading today's session…" : resource.status === "error" ? "Couldn't load today's session" : "Nothing scheduled today";
    const sub =
      resource.status === "loading"
        ? "Fetching your plan and current readiness."
        : resource.status === "error"
          ? "Reload to try again."
          : "Plan a block to get a recommended session, or take today as recovery.";
    return (
      <div className="flex-1">
        <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-ac">Recommended today</div>
        <div className="mt-[9px] text-[22px] font-bold leading-[1.1] text-ink">{headline}</div>
        <div className="mt-[9px] max-w-[380px] text-[13px] font-medium leading-[1.5] text-mute">{sub}</div>
        <div className="mt-4 flex gap-[10px]">
          <button onClick={onPlan} className={btnGhost}>Plan your week</button>
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
        {/* No "Start session" button. The guided player renders a hardcoded interval
            plan, and #183 established no real playable session can be built from the
            current prescription contract, so authenticated entry is closed (#188). */}
        <button onClick={onPlan} className={btnGhost}>View week</button>
      </div>
      {!primaryAction.canStartSession && primaryAction.unavailableReason && (
        <div className="mt-[10px] text-[11px] font-medium leading-[1.5] text-dim">{primaryAction.unavailableReason}</div>
      )}
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

// ---- Training load + habit --------------------------------------------------------

const ACWR_AXIS_MAX = 2;
const acwrPct = (v: number) => Math.max(0, Math.min(100, (v / ACWR_AXIS_MAX) * 100));

function TrainingLoadCard({ load }: { load: ReturnType<typeof loadSection> }) {
  const status = LOAD_STATUS[load.status];
  return (
    <Card>
      <div className="flex items-center justify-between">
        <SectionLabel className="text-faint">Training load</SectionLabel>
        <span className={`font-mono text-[11px] font-semibold leading-none ${status.color}`}>{status.label}</span>
      </div>
      <div className="mt-3 flex items-end gap-2">
        <span className="font-mono text-[30px] font-semibold leading-none text-ink">
          <MetricText state={load.acwr.kind === "value" ? { kind: "value", value: load.acwr.value.toFixed(2) } : load.acwr} />
        </span>
        <span className="mb-1 text-[11px] font-medium leading-none text-faint">ACWR · 7d/28d</span>
      </div>
      {load.sweetSpot.kind === "value" && (
        <>
          <Gauge
            variant="band"
            className="mt-3"
            height={6}
            band={{ start: acwrPct(load.sweetSpot.value.low) / 100, end: acwrPct(load.sweetSpot.value.high) / 100 }}
            pct={load.acwr.kind === "value" ? acwrPct(load.acwr.value) / 100 : 0}
            showMarker={load.acwr.kind === "value"}
          />
          <div className="mt-[7px] font-mono text-[10px] leading-none text-dim">
            sweet spot {load.sweetSpot.value.low}–{load.sweetSpot.value.high}
          </div>
        </>
      )}
    </Card>
  );
}

function HabitCard({ habit }: { habit: ReturnType<typeof habitSection> }) {
  return (
    <Card>
      <div className="flex items-center justify-between">
        <SectionLabel className="text-faint">Habit</SectionLabel>
        <span className="font-mono text-[11px] font-semibold leading-none text-ac">
          {habit.streakDays.kind === "value"
            ? habit.streakDays.value > 0
              ? `${habit.streakDays.value}-day streak`
              : "No streak yet"
            : ""}
        </span>
      </div>
      <div className="mt-3 flex items-end gap-2">
        <span className="font-mono text-[30px] font-semibold leading-none text-ink">
          <MetricText state={habit.pct.kind === "value" ? { kind: "value", value: `${Math.round(habit.pct.value)}%` } : habit.pct} />
        </span>
        <span className="mb-1 text-[11px] font-medium leading-none text-faint">adherence</span>
      </div>
      {/* No bar at all when adherence is unknown. A 0%-wide track is a claim that
          the athlete adhered to nothing, and ADR-0046 is explicit that 0 never
          encodes "unknown". */}
      {habit.pct.kind === "value" && <Track pct={habit.pct.value} className="mt-3 h-[6px]" />}
    </Card>
  );
}

// ---- Recent activity ---------------------------------------------------------------

function RecentActivity({ resource }: { resource: AuthedResource<WorkoutLogSummary[]> }) {
  switch (resource.status) {
    case "guest":
      return <EmptyLine>Sign in to see your logged sessions.</EmptyLine>;
    case "loading":
      return <EmptyLine>Loading recent sessions…</EmptyLine>;
    case "error":
      return <EmptyLine>Couldn&apos;t load your sessions — {resource.error.message}</EmptyLine>;
    case "success": {
      const workouts = resource.data;
      if (workouts.length === 0) return <EmptyLine>No workouts logged yet — log one to see it here.</EmptyLine>;
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
                  <div className="text-[13px] font-semibold leading-none text-ink">
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
  }
}

// ---- Insights -----------------------------------------------------------------------

function InsightsCard({
  readinessRes,
  todayRes,
}: {
  readinessRes: AuthedResource<ReadinessScore>;
  todayRes: AuthedResource<TodaySessionResponse>;
}) {
  const insights: { dot: string; title: string; desc: string }[] = [];

  const readiness = resourceData(readinessRes);
  if (readiness) {
    const comps = [...(readiness.components ?? [])].sort((a, b) => a.contribution - b.contribution);
    const worst = comps[0];
    const best = comps[comps.length - 1];
    if (worst && worst.contribution < -0.05) {
      insights.push({
        dot: COLORS.hot,
        title: `${signalLabel(worst.signal)} below baseline`,
        desc: `${worst.value} vs baseline ${Math.round(worst.baseline)} — pulling readiness down today.`,
      });
    }
    if (best && best !== worst && best.contribution > 0.05) {
      insights.push({
        dot: COLORS.good,
        title: `${signalLabel(best.signal)} above baseline`,
        desc: `${best.value} vs baseline ${Math.round(best.baseline)} — supporting readiness today.`,
      });
    }
  }

  const today = resourceData(todayRes);
  const why = (today ? today.prescription : null)?.why ?? null;
  // Labels, never engine codes (app/logic/constraint_labels.py). Bookkeeping stays hidden, and
  // advisories are already covered by `warnings` below.
  (why?.constraint_details ?? [])
    .filter((d) => d.athlete_visible && d.group !== "advisory" && d.group !== "internal")
    .forEach((d) =>
      insights.push({
        dot: d.group === "safety" ? COLORS.hot : COLORS.warn,
        title: d.group === "safety" ? "Safety adjustment" : "Plan adjustment",
        desc: d.label,
      }),
    );
  (why?.warnings ?? []).forEach((wn) => insights.push({ dot: COLORS.hot, title: "Heads up", desc: wn }));

  return (
    <Card>
      <div className="mb-[14px] flex items-center justify-between">
        <SectionLabel>Insights</SectionLabel>
        <span className="text-[11px] font-medium leading-none text-dim">from readiness &amp; your plan</span>
      </div>
      {insights.length === 0 ? (
        <EmptyLine>No insights yet — log a check-in or a workout and they&apos;ll appear here.</EmptyLine>
      ) : (
        <div className="flex flex-col gap-[2px]">
          {insights.map((a, i) => (
            <div key={i} className="flex items-start gap-3 border-b border-white/[0.05] py-[11px] last:border-0">
              <span className="mt-[3px] h-[9px] w-[9px] flex-none rounded-full" style={{ background: a.dot }} />
              <div>
                <div className="text-[13px] font-semibold leading-none text-ink">{a.title}</div>
                <div className="mt-1 text-[11.5px] font-medium leading-[1.5] text-mute">{a.desc}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ---- Objective hero ------------------------------------------------------------------

function GoalObjectiveCard() {
  const { state, actions } = usePerfLab();
  const objectivesRes = useAuthedResource<ObjectiveRead[]>((t) => api.listObjectives(t), [state.objectivesRefreshKey]);
  const objectives = resourceData(objectivesRes);
  const top = objectives && objectives.length ? sortObjectives(objectives)[0] : null;
  const gradient = { background: "radial-gradient(120% 140% at 100% 0%,#11321f,#111419 55%)" };

  if (!top) {
    const loading = objectivesRes.status === "loading";
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

/** Real cross-block "week X of Y" for the header subtitle. Renders nothing when
 *  there is no active program — never a fabricated week. */
export function ProgramWeek() {
  const { state } = usePerfLab();
  const macrosRes = useAuthedResource<MacrocycleRead[]>((t) => api.listMacrocycles(t), [state.macrocyclesRefreshKey]);
  const macro = activeMacrocycle(resourceData(macrosRes));
  if (!macro) return null;
  return <>&nbsp;·&nbsp; {weekProgressLabel(macro.week_progress)}</>;
}
