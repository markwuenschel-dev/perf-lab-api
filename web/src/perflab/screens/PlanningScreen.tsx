// src/perflab/screens/PlanningScreen.tsx
//
// Console PR 5 — Planning, planned vs logged.
//
// C1a "honesty pass" still governs this screen: the signed-in surface shows only
// live, backend-owned data — the real week (planned sessions + their lifecycle
// status + the workouts that fulfilled them), canonical readiness, the typed
// prescription, and the block's deload/benchmark cadence read off the planned
// sessions' own `is_deload` / `is_benchmark` flags. Guests keep a simulated
// preview, labelled as sample data end to end.
//
// C1b is now live: the Forward projection panel reads GET /v1/planning/projection
// (ADR-0073) — per-day sRPE load and modeled MEAN FATIGUE for the still-pending
// sessions. It is fatigue, never readiness (PDR-0005 / ADR-0064), and a session
// whose intensity was not prescribed yet is a `template_estimate` and is shown as
// one. `available: false` renders an explanation and never a chart.
import { useMemo, useState, type DragEvent, type ReactNode } from "react";
import * as api from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import { cn } from "@/lib/utils";
import { fmtDist } from "@/lib/units";
import type {
  BlockRead,
  PlannedSessionRead,
  PlannedSessionUpdateRequest,
  PlannedWeekProjection,
  PrescriptionRevisionRead,
  ReadinessScore,
  SessionStatus,
  WorkoutLogSummary,
  WorkoutPrescription,
} from "@/types";
import { usePerfLab } from "../store";
import { canGiveFeedback, isMovable, moveRequest, reopenFloorIso } from "../sessionActions";
import { dispositionTag } from "../workoutDisposition";
import { useAuthedResource } from "../useAuthedResource";
import { assertNever, toResourceError, type AuthedResource } from "../resource";
import { Card, MetricBar, ScreenHeader, SectionLabel, WeakPointTags } from "../ui";
import { WhyThisSession } from "../prescription/WhyThisSession";
import { LoadExplanation } from "../prescription/LoadExplanation";
import { ExpectedOutcomes } from "../prescription/ExpectedOutcomes";
import { PlanRevisionTriggers } from "../prescription/PlanRevisionTriggers";
import { RevisionNotice } from "../prescription/RevisionNotice";
import { Chart, Bars, Line, Marker, Axis, Legend, useVizTheme } from "../viz";
import { PHASES } from "../sim";
import { COLORS } from "../readinessPresentation";

// Console section label (mono 10px, faint) — a local override of the shared
// SectionLabel default, as on Twin / Simulator / History.
const LABEL = "text-[10px] text-faint";
const ROW_LABEL = "flex items-center font-mono text-[9px] font-semibold uppercase leading-[1.3] tracking-[0.1em] text-faint";
const SECONDARY_BTN = "rounded-[9px] border border-white/[0.07] bg-white/[0.04] px-[14px] py-[9px] text-[12.5px] font-semibold leading-none text-soft";
const PRIMARY_BTN = "rounded-[9px] bg-ac px-[15px] py-[9px] text-[12.5px] font-semibold leading-none text-[#0a0c10]";
const CELL_BTN = "mt-auto rounded-[7px] border border-white/[0.07] bg-white/[0.04] px-2 py-[6px] text-[10px] font-semibold leading-none text-soft disabled:opacity-50";

// ──────────────────────────────────────────────────────────────────────────
// Dates
// ──────────────────────────────────────────────────────────────────────────
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const isoLocal = (d: Date): string =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const titleCase = (s: string): string => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const humanize = (s: string): string => titleCase(s.replace(/_/g, " "));

// Parse a "YYYY-MM-DD" string as a LOCAL date (new Date(iso) would treat it as UTC
// midnight and can shift the day across timezones).
const parseIsoLocal = (iso: string): Date => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
};
const addDays = (d: Date, n: number): Date => {
  const out = new Date(d);
  out.setDate(d.getDate() + n);
  return out;
};
const dowOf = (iso: string): string => DOW[(parseIsoLocal(iso).getDay() + 6) % 7];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtDay = (iso: string): string => {
  const d = parseIsoLocal(iso);
  return `${dowOf(iso)} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
};

// The Mon–Sun window that contains `date`, plus its ISO bounds for the query.
function weekWindowFor(date: Date): { monday: Date; start_date: string; end_date: string } {
  const monday = new Date(date);
  monday.setDate(date.getDate() - ((date.getDay() + 6) % 7));
  monday.setHours(0, 0, 0, 0);
  return { monday, start_date: isoLocal(monday), end_date: isoLocal(addDays(monday, 6)) };
}

/** Per-session training load, sRPE (RPE × minutes). Mirrors the backend's
 *  `dashboard_service.daily_load` — the same proxy ACWR and the projection use —
 *  including its fallbacks (duration alone without RPE, 0 without duration). */
function sessionLoad(rpe: number | null | undefined, minutes: number | null | undefined): number {
  const dur = minutes ?? 0;
  if (dur <= 0) return 0;
  return rpe && rpe > 0 ? rpe * dur : dur;
}

const fmtLoad = (n: number): string => (n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : `${Math.round(n)}`);

// ──────────────────────────────────────────────────────────────────────────
// Week model — pure. One cell per day, a planned half and a logged half.
// ──────────────────────────────────────────────────────────────────────────
type LoggedState = "done" | "skipped" | "missed" | "pending" | "upcoming" | "rest";

interface PlannedHalf {
  sessionId?: number;
  title: string;
  sub: string;
  isDeload: boolean;
  isBenchmark: boolean;
  /** Only a session that has not happened can be moved. */
  movable: boolean;
  /** Moving it reopens a missed session (P2b), so its earliest destination is the server's
   *  today, not just the local one. */
  reopens: boolean;
  /** Further sessions on the same date (the strip shows the first). */
  extra: number;
}

interface LoggedHalf {
  state: LoggedState;
  title: string;
  sub: string;
  sessionId?: number;
  /** The status the server returned — what every action on this cell keys off (P2). A past
   *  `pending` session also renders as "missed" but has no outcome to give feedback on. */
  status?: SessionStatus;
}

interface DayCell {
  iso: string;
  day: string;
  today: boolean;
  planned: PlannedHalf | null;
  logged: LoggedHalf;
}

function loggedHalfFor(
  s: PlannedSessionRead | undefined,
  iso: string,
  todayIso: string,
  workoutsById: Map<number, WorkoutLogSummary> | null,
  units: string,
): LoggedHalf {
  if (!s) return { state: "rest", title: "—", sub: "rest day" };
  return { ...loggedView(s, iso, todayIso, workoutsById, units), status: s.status };
}

function loggedView(
  s: PlannedSessionRead,
  iso: string,
  todayIso: string,
  workoutsById: Map<number, WorkoutLogSummary> | null,
  units: string,
): LoggedHalf {
  switch (s.status) {
    case "completed": {
      const w = s.workout_log_id != null ? workoutsById?.get(s.workout_log_id) : undefined;
      if (!w) return { state: "done", title: "Completed", sub: "log details not loaded", sessionId: s.id };
      const first = [`RPE ${w.session_rpe}`, w.distance_meters > 0 ? fmtDist(w.distance_meters / 1000, units) : null]
        .filter(Boolean)
        .join(" · ");
      // P3a: a workout saved without a training-state update says so on its row.
      const tag = dispositionTag(w.state_disposition);
      return {
        state: "done",
        title: `${titleCase(w.modality)} · ${Math.round(w.duration_minutes)} min`,
        sub: `${first}\nload ${Math.round(sessionLoad(w.session_rpe, w.duration_minutes))}${tag ? `\n${tag}` : ""}`,
        sessionId: s.id,
      };
    }
    case "skipped":
      return { state: "skipped", title: "Skipped", sub: "marked skipped", sessionId: s.id };
    // P2: the server's own verdict on a past session nothing was logged for.
    case "missed":
      return { state: "missed", title: "Not logged", sub: "planned · missed", sessionId: s.id };
    // ADR-0069: a date move does not change status, but legacy rows may still
    // read `rescheduled` — either way the session has not happened yet.
    case "pending":
    case "rescheduled":
      if (iso < todayIso) return { state: "missed", title: "Not logged", sub: "planned · missed", sessionId: s.id };
      if (iso === todayIso) return { state: "pending", title: "Pending", sub: "prescribed today", sessionId: s.id };
      return { state: "upcoming", title: "Upcoming", sub: "not due yet", sessionId: s.id };
    default:
      return assertNever(s.status);
  }
}

function buildDayCells(
  monday: Date,
  sessions: PlannedSessionRead[],
  workoutsById: Map<number, WorkoutLogSummary> | null,
  units: string,
  todayIso: string,
): DayCell[] {
  const byDate = new Map<string, PlannedSessionRead[]>();
  for (const s of [...sessions].sort((a, b) => a.id - b.id)) {
    byDate.set(s.scheduled_date, [...(byDate.get(s.scheduled_date) ?? []), s]);
  }
  return DOW.map((day, i) => {
    const iso = isoLocal(addDays(monday, i));
    const onDay = byDate.get(iso) ?? [];
    const s = onDay[0];
    const planned: PlannedHalf | null = s
      ? {
          sessionId: s.id,
          title: humanize(s.category || s.modality),
          sub: [s.is_deload ? "deload" : null, humanize(s.modality)].filter(Boolean).join(" · "),
          isDeload: s.is_deload,
          isBenchmark: s.is_benchmark,
          movable: isMovable(s.status),
          reopens: s.status === "missed",
          extra: onDay.length - 1,
        }
      : null;
    return { iso, day, today: iso === todayIso, planned, logged: loggedHalfFor(s, iso, todayIso, workoutsById, units) };
  });
}

export function PlanningScreen() {
  const { token } = useAuth();
  return token ? <AuthedPlanningBody /> : <GuestPlanningPreview />;
}

// ──────────────────────────────────────────────────────────────────────────
// Authenticated: live-only surface.
// ──────────────────────────────────────────────────────────────────────────
function AuthedPlanningBody() {
  const { state, actions } = usePerfLab();
  const { token } = useAuth();
  const goal = state.settings.goal;

  // The displayed Mon–Sun window. Defaults to the current week, but after a block
  // is created it re-derives from that block's start_date (`planningWeekAnchor`).
  const week = useMemo(
    () => weekWindowFor(state.planningWeekAnchor ? parseIsoLocal(state.planningWeekAnchor) : new Date()),
    [state.planningWeekAnchor],
  );
  // Bumped after this screen's own writes (reschedule, mark skipped) so the week,
  // the block cadence and the projection re-read what the server now holds.
  const [writeKey, setWriteKey] = useState(0);
  const [write, setWrite] = useState<{ busyId: number | null; error: string | null }>({ busyId: null, error: null });

  // `planningRefreshKey` is bumped by BlockCreateModal after a successful
  // POST /v1/planning/blocks so a freshly created block's week shows up here.
  // `feedbackRefreshKey` is bumped after feedback is recorded — this list is what
  // renders the affordance, so it is the resource that has to re-read. This re-reads the
  // session LIST only; today's prescription is an immutable revision (P1) that the
  // Prescribed session card reads on its own.
  const sessions = useAuthedResource<PlannedSessionRead[]>(
    (t) => api.listPlannedSessions(t, { start_date: week.start_date, end_date: week.end_date }),
    [week.start_date, state.planningRefreshKey, state.feedbackRefreshKey, writeKey],
  );
  // The fulfilled sessions' summaries (RPE, minutes, distance) for the Logged row.
  const workouts = useAuthedResource<WorkoutLogSummary[]>((t) => api.listWorkouts(t, 60), [week.start_date, writeKey]);

  async function patchSession(id: number, body: PlannedSessionUpdateRequest, verb: string) {
    if (!token) return;
    setWrite({ busyId: id, error: null });
    try {
      await api.updatePlannedSession(id, body, token);
      setWrite({ busyId: null, error: null });
      setWriteKey((k) => k + 1);
    } catch (e) {
      setWrite({ busyId: null, error: `Couldn't ${verb}: ${toResourceError(e).message}` });
    }
  }

  const header = (
    <ScreenHeader title="Planning" subtitle="Planned against logged — each session is dosed against your current readiness and tissue load.">
      <ReadinessPill />
      <button onClick={actions.openBlockCreate} className={SECONDARY_BTN}>New block</button>
      {/* Check-in writes wellness, so it is offered to a signed-in athlete only. */}
      <button onClick={actions.openCheckin} className={SECONDARY_BTN}>Check in</button>
      <button onClick={actions.openLog} className={PRIMARY_BTN}>Log workout</button>
    </ScreenHeader>
  );

  // Week load / error / genuinely-no-block distinction: a fetch that's in-flight
  // or errored must not be mistaken for "no block" — "no block" is read only off
  // a payload that actually loaded.
  switch (sessions.status) {
    // `guest` cannot be observed here: PlanningScreen mounts this body only with
    // a token, and the hook resolves identity from the same auth context in the
    // same commit. It reads as "not resolved yet".
    case "guest":
    case "loading":
      return <PlanningNotice title="Loading your plan…" body="Fetching this week's prescribed sessions." />;

    case "error":
      return <PlanningNotice title="Couldn't load your plan" body={sessions.error.message} onRetry={() => actions.focusPlanningWeek(week.start_date)} />;

    case "success": {
      if (sessions.data.length === 0) return <PlanningEmptyState onCreate={actions.openBlockCreate} />;

      const todayIso = isoLocal(new Date());
      const workoutsById = workouts.status === "success" ? new Map(workouts.data.map((w) => [w.id, w])) : null;
      const cells = buildDayCells(week.monday, sessions.data, workoutsById, state.settings.units, todayIso);
      const planned = sessions.data.length;
      const completed = sessions.data.filter((s) => s.status === "completed").length;

      // The block the displayed week belongs to (the first session's; a week
      // spans one block in practice) and this week's number within it.
      const anchor = [...sessions.data].sort((a, b) => a.id - b.id)[0];

      // Logged training inside the displayed week — all of it, planned or not.
      const inWeek = workoutsById
        ? [...workoutsById.values()].filter((w) => {
            const d = isoLocal(new Date(w.session_timestamp));
            return d >= week.start_date && d <= week.end_date;
          })
        : null;

      // Project the displayed week through its Sunday when that is a window the
      // endpoint accepts (today…today+28); otherwise let the server pick its
      // default, and the panel states whichever window it actually got.
      const through =
        week.end_date >= todayIso && week.end_date <= isoLocal(addDays(parseIsoLocal(todayIso), 28)) ? week.end_date : undefined;

      return (
        <section className="flex flex-col gap-[14px] px-[30px] pb-9 pt-[26px]">
          {header}

          <LiveBlockContextBar blockId={anchor.block_id} weekNumber={anchor.week_number} refreshKey={state.planningRefreshKey + writeKey} />

          <WeekGrid
            cells={cells}
            todayIso={todayIso}
            summary={`${completed} of ${planned} planned session${planned === 1 ? "" : "s"} logged · drag a card to reschedule`}
            busyId={write.busyId}
            error={write.error}
            onMove={(id, iso) =>
              patchSession(id, moveRequest(sessions.data.find((s) => s.id === id)?.status, iso), "move the session")
            }
            onSkip={(id) => patchSession(id, { status: "skipped" }, "mark the session skipped")}
            onLog={actions.openLog}
            onFeedback={(id, status) => actions.openFeedback(id, status)}
          />

          <div className="grid grid-cols-1 items-start gap-[14px] lg:grid-cols-[minmax(0,1fr)_320px]">
            {/* live prescribed session + its rationale/explanation */}
            <PrescribedSessionCard goal={goal} />
            <div className="flex flex-col gap-[14px]">
              <WeekLoadCard
                rows={[
                  { label: "Sessions", value: `${completed} / ${planned}`, plannedPct: 100, loggedPct: planned ? (completed / planned) * 100 : 0 },
                  { label: "Minutes", value: inWeek ? `${Math.round(inWeek.reduce((a, w) => a + w.duration_minutes, 0))}` : "—" },
                  { label: "Load", value: inWeek ? fmtLoad(inWeek.reduce((a, w) => a + sessionLoad(w.session_rpe, w.duration_minutes), 0)) : "—" },
                ]}
              />
              <LiveForwardProjection through={through} refreshKey={state.planningRefreshKey + writeKey} />
            </div>
          </div>
        </section>
      );
    }

    default:
      return assertNever(sessions);
  }
}

// The one backend-owned readiness number (getReadiness → ReadinessScore.score,
// PDR-0005-safe). Fetches independently: a readiness failure degrades to
// "Readiness unavailable" without touching the week strip or session card.
function ReadinessPill() {
  const { state } = usePerfLab();
  const readiness = useAuthedResource<ReadinessScore>((t) => api.getReadiness(t), [state.readinessRefreshKey]);
  const { label, known } = readinessPillView(readiness);
  return <ReadinessChip label={label} known={known} />;
}

function ReadinessChip({ label, known }: { label: string; known: boolean }) {
  return (
    <div className="flex items-center gap-[7px] rounded-[9px] border border-ac/25 bg-ac/[0.1] px-[13px] py-[9px] font-mono text-[11px] font-semibold leading-none text-ac">
      {known && <span className="h-[7px] w-[7px] rounded-full bg-ac" />}
      {label}
    </div>
  );
}

// The pill is one inline chip, not a card, so it consumes the contract through
// an exhaustive switch. `known` (the lit dot) stays tied to an actual number
// being on screen: no dot for guest, in-flight, failed, or a payload whose
// `score` is null.
function readinessPillView(resource: AuthedResource<ReadinessScore>): { label: string; known: boolean } {
  switch (resource.status) {
    case "loading":
      return { label: "Readiness…", known: false };

    // Unreachable (authenticated surface), and indistinguishable to the athlete
    // from a failed read: either way there is no readiness to state.
    case "guest":
    case "error":
      return { label: "Readiness unavailable", known: false };

    case "success": {
      const { score, band } = resource.data;
      if (score == null) return { label: "Readiness unavailable", known: false };
      return { label: `Readiness ${Math.round(score)}${band ? ` · ${titleCase(band)}` : ""}`, known: true };
    }

    default:
      return assertNever(resource);
  }
}

// ──────────────────────────────────────────────────────────────────────────
// Block context bar — cadence read off the block's own planned sessions.
// ──────────────────────────────────────────────────────────────────────────
interface BlockContext {
  block: BlockRead | null;
  deloadWeeks: number[];
  benchmarkWeeks: number[];
}

function LiveBlockContextBar({ blockId, weekNumber, refreshKey }: { blockId: number; weekNumber: number; refreshKey: number }) {
  const ctx = useAuthedResource<BlockContext>(
    async (t) => {
      const block = (await api.listPlanningBlocks(t)).find((b) => b.id === blockId) ?? null;
      if (!block) return { block: null, deloadWeeks: [], benchmarkWeeks: [] };
      const end = block.end_date ?? isoLocal(addDays(parseIsoLocal(block.start_date), block.duration_weeks * 7 - 1));
      const all = (await api.listPlannedSessions(t, { start_date: block.start_date, end_date: end })).filter((s) => s.block_id === blockId);
      const weeks = (pred: (s: PlannedSessionRead) => boolean) => [...new Set(all.filter(pred).map((s) => s.week_number))].sort((a, b) => a - b);
      return { block, deloadWeeks: weeks((s) => s.is_deload), benchmarkWeeks: weeks((s) => s.is_benchmark) };
    },
    [blockId, refreshKey],
  );

  switch (ctx.status) {
    case "guest":
      return null;
    case "loading":
      return <BlockBarShell><span className="text-[12px] font-medium text-mute">Loading block…</span></BlockBarShell>;
    case "error":
      return <BlockBarShell><span className="text-[12px] font-medium text-mute">Block context unavailable — {ctx.error.message}</span></BlockBarShell>;
    case "success": {
      const { block, deloadWeeks, benchmarkWeeks } = ctx.data;
      if (!block) return <BlockBarShell><span className="text-[12px] font-medium text-mute">Block details unavailable for this week.</span></BlockBarShell>;
      return (
        <BlockBarView
          name={`${humanize(block.goal)} block`}
          weekNumber={weekNumber}
          durationWeeks={block.duration_weeks}
          deloadWeeks={deloadWeeks}
          benchmarkWeeks={benchmarkWeeks}
        />
      );
    }
    default:
      return assertNever(ctx);
  }
}

function BlockBarShell({ children }: { children: ReactNode }) {
  return <div className="flex items-center gap-[22px] rounded-[14px] border border-white/[0.07] bg-tile px-[18px] py-[14px]">{children}</div>;
}

function relWeek(w: number, current: number): string {
  if (w === current) return " (this week)";
  if (w === current + 1) return " (next week)";
  return "";
}

function BlockBarView({
  name,
  weekNumber,
  durationWeeks,
  deloadWeeks,
  benchmarkWeeks,
}: {
  name: string;
  weekNumber: number;
  durationWeeks: number;
  deloadWeeks: number[];
  benchmarkWeeks: number[];
}) {
  const total = Math.max(1, durationWeeks);
  const pct = (w: number) => Math.min(100, Math.max(0, (w / total) * 100));
  const nextDeload = deloadWeeks.find((w) => w >= weekNumber);
  const nextBenchmark = benchmarkWeeks.find((w) => w >= weekNumber);
  return (
    <BlockBarShell>
      <div className="flex-none">
        <SectionLabel className="text-[9.5px] text-faint">Active block</SectionLabel>
        <div className="mt-2 text-[14px] font-bold leading-none text-ink">{name}</div>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex justify-between font-mono text-[9.5px] leading-none tracking-[0.1em] text-dim">
          <span>wk 1</span>
          <span>wk {total}</span>
        </div>
        <div
          className="relative mt-[7px] h-2 overflow-hidden rounded-full bg-white/[0.08]"
          role="img"
          aria-label={`Block week ${weekNumber} of ${total}`}
        >
          <div className="h-full rounded-full bg-ac" style={{ width: `${pct(weekNumber)}%` }} />
          {nextDeload != null && (
            <div data-marker="deload" className="absolute bottom-0 top-0 w-[2px] -translate-x-[2px] bg-warn" style={{ left: `${pct(nextDeload)}%` }} />
          )}
          {nextBenchmark != null && (
            <div data-marker="benchmark" className="absolute bottom-0 top-0 w-[2px] -translate-x-[2px] bg-cat-5" style={{ left: `${pct(nextBenchmark)}%` }} />
          )}
        </div>
        <div className="mt-2 flex flex-wrap gap-x-[18px] gap-y-1 text-[11px] font-medium leading-none text-mute">
          {nextDeload != null && (
            <span><span className="mr-[6px] inline-block h-2 w-2 rounded-[2px] bg-warn" />deload · wk {nextDeload}{relWeek(nextDeload, weekNumber)}</span>
          )}
          {nextBenchmark != null && (
            <span><span className="mr-[6px] inline-block h-2 w-2 rounded-[2px] bg-cat-5" />benchmark week · wk {nextBenchmark}{relWeek(nextBenchmark, weekNumber)}</span>
          )}
          {nextDeload == null && nextBenchmark == null && <span>no deload or benchmark week left in this block</span>}
        </div>
      </div>
      <div className="flex-none text-right">
        <div className="font-mono text-[22px] font-semibold leading-none text-ink">{weekNumber} / {total}</div>
        <SectionLabel className="mt-[6px] text-[9px] text-faint">block week</SectionLabel>
      </div>
    </BlockBarShell>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// This week — planned above, logged below.
// ──────────────────────────────────────────────────────────────────────────
function WeekGrid({
  cells,
  todayIso,
  summary,
  busyId = null,
  error = null,
  onMove,
  onSkip,
  onLog,
  onFeedback,
}: {
  cells: DayCell[];
  todayIso: string;
  summary: string;
  busyId?: number | null;
  error?: string | null;
  /** Absent on the guest preview: nothing there is a real session to write to. */
  onMove?: (sessionId: number, iso: string) => void;
  onSkip?: (sessionId: number) => void;
  onLog?: () => void;
  onFeedback?: (sessionId: number, status: SessionStatus) => void;
}) {
  const [dragId, setDragId] = useState<number | null>(null);
  const [overIso, setOverIso] = useState<string | null>(null);

  // A session may land on a day of this week that is today or later and has no
  // planned session of its own. Moving into the past would only manufacture a
  // "missed" session; stacking two on one day hides one in this strip.
  // A missed session is reopened by its move, which the server allows only from ITS today.
  const reopenFloor = reopenFloorIso(todayIso);
  const reopensById = new Map(cells.flatMap((c) => (c.planned?.sessionId != null ? [[c.planned.sessionId, c.planned.reopens] as const] : [])));
  const canDropOn = (c: DayCell, sessionId: number | null): boolean =>
    onMove != null &&
    c.iso >= (sessionId != null && reopensById.get(sessionId) ? reopenFloor : todayIso) &&
    c.planned == null;

  const drop = (c: DayCell, e: DragEvent) => {
    e.preventDefault();
    const raw = dragId ?? Number(e.dataTransfer?.getData("text/plain"));
    setDragId(null);
    setOverIso(null);
    if (!raw || !canDropOn(c, raw)) return;
    onMove?.(raw, c.iso);
  };

  return (
    <Card className="p-[18px]">
      <div className="mb-[14px] flex items-center justify-between gap-3">
        <SectionLabel className={LABEL}>This week</SectionLabel>
        <div className="text-[11px] font-medium leading-none text-dim">{summary}</div>
      </div>
      {error && <div role="alert" className="mb-3 text-[11.5px] font-medium leading-[1.4] text-hot">{error}</div>}
      <div className="grid grid-cols-[62px_repeat(7,minmax(0,1fr))] items-stretch gap-2">
        <div className={ROW_LABEL}>Planned</div>
        {cells.map((c, i) => {
          const p = c.planned;
          const draggable = onMove != null && p?.movable === true && p.sessionId != null && busyId == null;
          const droppable = dragId != null && canDropOn(c, dragId);
          const next = cells[i + 1];
          const nextOk = draggable && next != null && canDropOn(next, p?.sessionId ?? null);
          return (
            <div
              key={c.iso}
              data-day={c.iso}
              data-slot="planned"
              draggable={draggable}
              onDragStart={(e) => {
                if (!draggable || p?.sessionId == null) return;
                setDragId(p.sessionId);
                e.dataTransfer?.setData("text/plain", String(p.sessionId));
              }}
              onDragEnd={() => {
                setDragId(null);
                setOverIso(null);
              }}
              onDragOver={(e) => {
                if (!droppable) return;
                e.preventDefault();
                if (overIso !== c.iso) setOverIso(c.iso);
              }}
              onDragLeave={() => { if (overIso === c.iso) setOverIso(null); }}
              onDrop={(e) => drop(c, e)}
              {...(c.today ? {} : { "data-tile": "1" })}
              className={cn(
                "flex min-h-[96px] flex-col rounded-[12px] border px-[10px] pb-[10px] pt-3",
                c.today ? "border-ac/[0.45] bg-ac/[0.07]" : "border-white/[0.07]",
                droppable && "border-dashed border-ac/40",
                overIso === c.iso && droppable && "bg-ac/[0.07]",
                draggable && "cursor-grab",
                busyId != null && p?.sessionId === busyId && "opacity-50",
              )}
            >
              <div className="flex items-center justify-between">
                <span className={cn("font-mono text-[10px] uppercase leading-none", c.today ? "text-ac" : "text-faint")}>{c.day}</span>
                {c.today && <span className="rounded-[5px] bg-ac px-[5px] py-[3px] font-mono text-[8px] leading-none text-[#0a0c10]">TODAY</span>}
              </div>
              {p?.isBenchmark && <span className="mt-2 w-fit rounded-[5px] bg-cat-5/15 px-[5px] py-[3px] font-mono text-[8px] uppercase leading-none text-cat-5">benchmark</span>}
              <div className={cn("mt-auto text-[12px] font-semibold leading-[1.3]", c.today ? "text-ink" : p ? "text-mute" : "text-faint")}>{p ? p.title : "Rest"}</div>
              {p?.sub && <div className="mt-[5px] text-[10px] font-medium leading-none text-teal">{p.sub}</div>}
              {p && p.extra > 0 && <div className="mt-1 font-mono text-[9.5px] leading-none text-dim">+{p.extra} more</div>}
              {nextOk && p?.sessionId != null && (
                <button
                  onClick={() => onMove?.(p.sessionId!, next.iso)}
                  aria-label={`Move ${p.title} to ${next.day}`}
                  className="mt-2 w-fit rounded-[6px] border border-white/[0.07] bg-white/[0.04] px-[6px] py-[4px] font-mono text-[9px] leading-none text-mute"
                >
                  +1 day
                </button>
              )}
            </div>
          );
        })}

        <div className={ROW_LABEL}>Logged</div>
        {cells.map((c) => {
          const l = c.logged;
          const busy = busyId != null && l.sessionId === busyId;
          return (
            <div
              key={c.iso}
              data-day={c.iso}
              data-slot="logged"
              data-state={l.state}
              className={cn(
                "flex min-h-[74px] flex-col rounded-[12px] border p-[10px]",
                l.state === "done" && "border-white/[0.07] bg-tile",
                l.state === "missed" && "border-dashed border-hot/40",
                l.state === "pending" && "border-dashed border-ac/40",
                (l.state === "skipped" || l.state === "upcoming" || l.state === "rest") && "border-white/[0.07]",
                l.state === "upcoming" && "border-dashed",
              )}
            >
              <div
                className={cn(
                  "text-[11.5px] font-semibold leading-[1.3]",
                  l.state === "done" ? "text-ink" : l.state === "missed" ? "text-hot" : l.state === "pending" ? "text-ac" : l.state === "skipped" ? "text-mute" : "text-dim",
                )}
              >
                {l.title}
              </div>
              <div className={cn("mt-[6px] whitespace-pre-line font-mono text-[10px] leading-[1.4]", l.state === "rest" ? "text-dim" : "text-faint")}>{l.sub}</div>
              {l.state === "missed" && onSkip && l.sessionId != null && (
                <button disabled={busyId != null} onClick={() => onSkip(l.sessionId!)} className={CELL_BTN}>{busy ? "Saving…" : "Mark skipped"}</button>
              )}
              {l.state === "pending" && onLog && (
                <button onClick={onLog} className={CELL_BTN}>Log workout</button>
              )}
              {/* Feedback needs a session that actually has an outcome. The id
                  comes from this cell's own row, never from whatever the
                  prescription card happens to be showing. */}
              {l.status != null && canGiveFeedback(l.status) && onFeedback && l.sessionId != null && (
                <button onClick={() => onFeedback(l.sessionId!, l.status!)} className={CELL_BTN}>Feedback</button>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Week load — planned vs logged. Only "Sessions" has a planned figure: planned
// sessions carry no dose until the day they are prescribed (ADR-0073), so
// minutes and load are logged-only rather than compared against an invention.
// ──────────────────────────────────────────────────────────────────────────
interface WeekLoadRow {
  label: string;
  value: string;
  plannedPct?: number;
  loggedPct?: number;
}

function WeekLoadCard({ rows, sample }: { rows: WeekLoadRow[]; sample?: boolean }) {
  return (
    <Card>
      <SectionLabel className={LABEL}>Week load · planned vs logged{sample ? " · sample" : ""}</SectionLabel>
      <div className="mt-4 flex flex-col gap-[11px]">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center gap-[10px]">
            <span className="w-[58px] flex-none text-[11.5px] font-medium leading-none text-mute">{r.label}</span>
            {r.plannedPct != null ? (
              <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-white/[0.08]">
                <div className="absolute bottom-0 left-0 top-0 rounded-full bg-ac opacity-[0.28]" style={{ width: `${r.plannedPct}%` }} />
                <div className="absolute bottom-0 left-0 top-0 rounded-full bg-ac" style={{ width: `${r.loggedPct ?? 0}%` }} />
              </div>
            ) : (
              <span className="flex-1 font-mono text-[9.5px] leading-none text-dim">logged only</span>
            )}
            <span className="w-[66px] text-right font-mono text-[11px] font-semibold leading-none text-soft">{r.value}</span>
          </div>
        ))}
      </div>
      <div className="mt-[14px] flex gap-4 border-t border-white/[0.07] pt-3 text-[10.5px] font-medium leading-none text-mute">
        <span><span className="mr-[6px] inline-block h-[6px] w-3 rounded-[2px] bg-ac" />logged</span>
        <span><span className="mr-[6px] inline-block h-[6px] w-3 rounded-[2px] bg-ac opacity-[0.28]" />planned</span>
      </div>
      <p className="mt-3 text-[10.5px] font-medium leading-[1.5] text-dim">
        Planned sessions carry no dose until they're prescribed, so minutes and load are what you logged this week.
      </p>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Forward projection (C1b, ADR-0073) — load vs modeled fatigue, never readiness.
// ──────────────────────────────────────────────────────────────────────────
function LiveForwardProjection({ through, refreshKey }: { through?: string; refreshKey: number }) {
  const projection = useAuthedResource<PlannedWeekProjection>((t) => api.getPlannedWeekProjection(t, through), [through, refreshKey]);
  return (
    <ProjectionShell>
      <ProjectionResourceBody resource={projection} />
    </ProjectionShell>
  );
}

function ProjectionShell({ children, sample }: { children: ReactNode; sample?: boolean }) {
  return (
    <Card>
      <div className="flex items-center justify-between gap-2">
        <SectionLabel className={LABEL}>Forward projection{sample ? " · sample" : ""}</SectionLabel>
        <span className="font-mono text-[9.5px] leading-none text-dim">load vs modeled fatigue</span>
      </div>
      {children}
    </Card>
  );
}

function ProjectionResourceBody({ resource }: { resource: AuthedResource<PlannedWeekProjection> }) {
  switch (resource.status) {
    case "guest":
      return null;
    case "loading":
      return <ProjectionNote>Projecting your pending sessions…</ProjectionNote>;
    case "error":
      return <ProjectionNote>Couldn't load the projection — {resource.error.message}</ProjectionNote>;
    case "success":
      return <ProjectionView data={resource.data} />;
    default:
      return assertNever(resource);
  }
}

function ProjectionNote({ children }: { children: ReactNode }) {
  return <div className="mt-3 text-[12px] font-medium leading-[1.55] text-mute">{children}</div>;
}

const UNAVAILABLE_COPY: Record<NonNullable<PlannedWeekProjection["reason"]>, string> = {
  no_state: "There's no modeled state to project from yet — log a workout or run a field test to seed your twin. Nothing is simulated in its place.",
  state_invalid: "Your stored state couldn't be read cleanly, so nothing is projected rather than guessed.",
};

function ProjectionView({ data }: { data: PlannedWeekProjection }) {
  const { accent, colors } = useVizTheme();
  const range = `${fmtDay(data.window.start)} → ${fmtDay(data.window.end)}`;

  if (!data.available) {
    return <ProjectionNote>{UNAVAILABLE_COPY[data.reason ?? "no_state"]}</ProjectionNote>;
  }
  const days = data.days ?? [];
  const withSessions = days.filter((d) => (d.sessions ?? []).length > 0);
  if (days.length === 0 || withSessions.length === 0) {
    return <ProjectionNote>No pending sessions left to project ({range}).</ProjectionNote>;
  }

  const isEstimate = (d: (typeof days)[number]) => (d.sessions ?? []).some((s) => s.basis === "template_estimate");
  const estimated = days.flatMap((d) => d.sessions ?? []).filter((s) => s.basis === "template_estimate").length;
  const total = days.reduce((a, d) => a + (d.sessions ?? []).length, 0);
  const labels = days.map((d) => dowOf(d.date));
  const loadMax = Math.max(1, ...days.map((d) => d.load)) * 1.1;
  const fatigueMax = Math.max(20, Math.ceil((Math.max(...days.map((d) => d.mean_fatigue)) * 1.25) / 10) * 10);
  const peak = data.peak_mean_fatigue ?? Math.max(...days.map((d) => d.mean_fatigue));

  return (
    <div className="mt-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[10px] leading-none text-dim">{range}</span>
        <span className="text-[11px] font-medium leading-none text-mute">
          Peak modeled fatigue <span className="font-mono font-semibold text-soft">{Math.round(peak)}</span>
        </span>
      </div>
      <Legend
        className="mt-3"
        items={[
          { label: "load (sRPE)", color: colors.categorical[1], mark: "rect" },
          { label: "mean fatigue (modeled)", color: accent, mark: "line" },
        ]}
      />
      <Chart width={280} height={70} padding={{ top: 6, right: 6, bottom: 2, left: 6 }} yDomain={[0, loadMax]} ariaLabel="Projected daily training load" className="mt-2 h-[70px] w-full">
        {/* Two layers on one band scale: prescribed days solid, days carrying a
            template estimate faded — the estimate is visibly an estimate. */}
        <Bars data={days.map((d) => ({ key: d.date, label: dowOf(d.date), value: isEstimate(d) ? 0 : d.load }))} color="series" baseColor={colors.categorical[1]} />
        <g opacity={0.4}>
          <Bars data={days.map((d) => ({ key: d.date, label: `${dowOf(d.date)} (estimate)`, value: isEstimate(d) ? d.load : 0 }))} color="series" baseColor={colors.categorical[1]} />
        </g>
      </Chart>
      <Chart
        width={280}
        height={70}
        padding={{ top: 6, right: 6, bottom: 18, left: 6 }}
        xDomain={[-0.5, days.length - 0.5]}
        yDomain={[0, fatigueMax]}
        ariaLabel="Projected modeled mean fatigue"
        className="h-[70px] w-full"
      >
        <Axis x xLabels={labels} />
        <Line data={days.map((d, i) => [i, d.mean_fatigue] as [number, number])} color={accent} width={2} label="mean fatigue" />
        {days.map((d, i) => (
          <Marker key={d.date} x={i} y={d.mean_fatigue} color={accent} r={3} />
        ))}
      </Chart>
      <ul className="mt-3 flex flex-col gap-[7px] border-t border-white/[0.07] pt-3">
        {withSessions.map((d) => (
          <li key={d.date} className="flex items-baseline justify-between gap-2 text-[11px] leading-none">
            <span className="font-medium text-mute">
              {dowOf(d.date)} · {(d.sessions ?? []).map((s) => humanize(s.modality)).join(", ")}
              {isEstimate(d) && <span className="ml-[6px] font-mono text-[9.5px] text-warn">estimate</span>}
            </span>
            <span className="font-mono text-soft">
              load {Math.round(d.load)} · fatigue {Math.round(d.mean_fatigue)}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[10.5px] font-medium leading-[1.5] text-dim">
        {estimated > 0
          ? `${estimated} of ${total} session${total === 1 ? "" : "s"} ${estimated === 1 ? "is a template estimate" : "are template estimates"} (faded) — nothing is prescribed yet, so the numbers will move once ${estimated === 1 ? "it is" : "they are"}. `
          : ""}
        Modeled fatigue, not readiness: future wellness is unknown, so no readiness is forecast.
      </p>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Prescribed session (P1). With a session planned today this is today's ISSUED revision
// (GET /v1/planning/today) — the same revision and content Overview and the Log Workout
// pre-fill show. Only when nothing is planned today does it fall back to a labelled preview
// (GET /v1/next-session). A prescription failure is localized to this card and never
// restores fixtures.
// ──────────────────────────────────────────────────────────────────────────
interface TodayPrescription {
  prescription: WorkoutPrescription;
  revision: PrescriptionRevisionRead | null;
  /** False: nothing is planned today, so this is a preview, not an issued session. */
  planned: boolean;
}

function PrescribedSessionCard({ goal }: { goal: string }) {
  const { token } = useAuth();
  const [todayKey, setTodayKey] = useState(0);
  const today = useAuthedResource<TodayPrescription>(async (t) => {
    const res = await api.getTodayPlannedSession(goal, t);
    if (res.prescription) {
      return { prescription: res.prescription, revision: res.revision ?? null, planned: true };
    }
    return { prescription: await api.getNextSession(goal, t), revision: null, planned: false };
  }, [goal, todayKey]);
  const prescription: AuthedResource<WorkoutPrescription> =
    today.status === "success" ? { ...today, data: today.data.prescription } : today;
  const revision = today.status === "success" ? today.data.revision : null;
  const preview = today.status === "success" && !today.data.planned;
  const recheck = async () => {
    if (!token) return;
    await api.recheckTodayPlannedSession(goal, token);
    setTodayKey((k) => k + 1);
  };

  // The card shell and its header render in every state — only the interior and
  // the header's right-hand summary are state-dependent — so this surface reads
  // the contract through exhaustive switches rather than <ResourceState>.
  return (
    <Card className="p-[22px]">
      <div className="flex items-center justify-between">
        <SectionLabel className={LABEL}>{preview ? "Preview — nothing planned today" : "Prescribed session"}</SectionLabel>
        <div className="font-mono text-[10px] leading-none text-dim">{prescriptionSummary(prescription)}</div>
      </div>

      <RevisionNotice revision={revision} onRecheck={recheck} />
      <PrescribedSessionBody resource={prescription} />
    </Card>
  );
}

function prescriptionSummary(resource: AuthedResource<WorkoutPrescription>): string {
  switch (resource.status) {
    case "loading":
      return "loading…";
    // Nothing to summarize, and nothing to imply: the header stays blank.
    case "guest":
    case "error":
      return "";
    case "success":
      return `${resource.data.type} · ${resource.data.duration_min} min`;
    default:
      return assertNever(resource);
  }
}

function PrescribedSessionBody({ resource }: { resource: AuthedResource<WorkoutPrescription> }) {
  const { actions } = usePerfLab();
  switch (resource.status) {
    // Unreachable (the card only mounts inside the authenticated body) and
    // deliberately silent: a guest is told nothing here.
    case "guest":
      return null;

    case "loading":
      return <div className="mt-4 text-[13px] font-medium text-mute">Computing your prescription…</div>;

    case "error":
      return (
        <div className="mt-4 text-[12.5px] font-medium leading-[1.5] text-mute">
          No live prescription yet — log a workout or run a field test to seed your twin.
        </div>
      );

    case "success": {
      const rx = resource.data;
      return (
        <div className="mt-4 flex flex-col gap-4">
          <div>
            <div className="text-[22px] font-bold leading-tight text-ink">{rx.focus}</div>
            <div className="mt-2 text-[12.5px] font-medium leading-[1.6] text-mute">{rx.rationale}</div>
          </div>

          {rx.exercises && rx.exercises.length > 0 && (
            <div className="flex flex-col gap-3 border-t border-white/[0.07] pt-4">
              {rx.exercises.map((ex, i) => {
                const detail = [
                  ex.sets != null && ex.reps != null
                    ? `${ex.sets}×${ex.reps}`
                    : ex.reps ?? (ex.sets != null ? `${ex.sets} sets` : ""),
                  ex.load_note,
                ]
                  .filter(Boolean)
                  .join(" · ");
                const tags = ex.weak_point_tags ?? [];
                return (
                  <div key={i} className="flex flex-col gap-[6px]">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-[13px] font-semibold leading-none text-soft">{ex.name}</span>
                      {detail && <span className="font-mono text-[12px] leading-none text-faint">{detail}</span>}
                    </div>
                    <LoadExplanation
                      explanation={ex.load_explanation}
                      exerciseName={ex.name}
                      onOpenAssess={() => actions.setScreen("assess")}
                    />
                    <WeakPointTags tags={tags} />
                  </div>
                );
              })}
            </div>
          )}

          <WhyThisSession why={rx.why} />
          <ExpectedOutcomes
            outcomes={rx.why?.expected_outcomes ?? []}
            horizon={rx.why?.expected_outcome_horizon}
          />
          <PlanRevisionTriggers triggers={rx.why?.plan_revision_triggers ?? []} />
        </div>
      );
    }

    default:
      return assertNever(resource);
  }
}

// Loading / error placeholder — kept visually distinct from the no-block CTA so a
// fetch that's still in-flight or that errored isn't mistaken for "create a block".
function PlanningNotice({ title, body, onRetry }: { title: string; body: string; onRetry?: () => void }) {
  return (
    <section className="flex min-h-[70vh] items-center justify-center px-[30px] pb-9 pt-[26px]">
      <Card className="flex max-w-[520px] flex-col items-center gap-4 p-[44px] text-center">
        <div className="text-[20px] font-bold leading-[1.2] text-ink">{title}</div>
        <div className="max-w-[380px] text-[13.5px] font-medium leading-[1.6] text-mute">{body}</div>
        {onRetry && (
          <button onClick={onRetry} className="mt-[6px] rounded-[10px] border border-white/[0.07] bg-white/[0.04] px-5 py-3 text-[13px] font-semibold leading-none text-soft">Retry</button>
        )}
      </Card>
    </section>
  );
}

// Replaces the dead-end where a fresh signed-in athlete with no block silently
// saw the hard-coded prototype week and had no way to get a real one.
function PlanningEmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <section className="flex min-h-[70vh] items-center justify-center px-[30px] pb-9 pt-[26px]">
      <Card className="flex max-w-[520px] flex-col items-center gap-4 p-[44px] text-center">
        <div className="grid h-[60px] w-[60px] place-items-center rounded-[16px] border border-ac/25 bg-ac/[0.1]">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="1.6"><path d="M12 2 4 7v10l8 5 8-5V7z" /><path d="M12 22V12M4 7l8 5 8-5" /></svg>
        </div>
        <div className="text-[22px] font-bold leading-[1.2] text-ink">No active training block</div>
        <div className="max-w-[380px] text-[13.5px] font-medium leading-[1.6] text-mute">Create a training block to get a week of sessions prescribed against your readiness — pick a goal, cadence and session-length preferences.</div>
        <button onClick={onCreate} className="mt-[6px] rounded-[10px] bg-ac px-5 py-3 text-[13px] font-semibold leading-none text-[#0a0c10]">Create a training block →</button>
      </Card>
    </section>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Guest: the simulated preview, labelled as sample data end-to-end. The only
// place fixture numbers render — authenticated users never see them.
// ──────────────────────────────────────────────────────────────────────────
const GUEST_DOSE: [string, number, number, string][] = [
  ["Volume", 5.5, 55, COLORS.teal],
  ["Intensity", 7.2, 72, "var(--ac)"],
  ["Density", 6.0, 60, "var(--ac)"],
  ["Impact", 4.0, 40, COLORS.warn],
  ["Skill", 2.2, 22, COLORS.good],
  ["Metabolic", 6.8, 68, "var(--ac)"],
];

// Sample week, Wednesday = "today". Dates are nominal (Mon 2026-01-05 …).
const GUEST_ISO = DOW.map((_, i) => isoLocal(addDays(new Date(2026, 0, 5), i)));
const GUEST_TODAY = GUEST_ISO[2];
const g = (title: string, sub: string, extra: Partial<PlannedHalf> = {}): PlannedHalf => ({
  title, sub, isDeload: false, isBenchmark: false, movable: false, reopens: false, extra: 0, ...extra,
});
const GUEST_CELL_DATA: Omit<DayCell, "iso" | "day" | "today">[] = [
  { planned: g("Recovery", "Run · Z1"), logged: { state: "done", title: "Run · 44 min", sub: "RPE 4 · 8.0 km\nload 176" } },
  { planned: g("Endurance", "Run · Z2"), logged: { state: "done", title: "Run · 70 min", sub: "RPE 5 · 14.1 km\nload 350" } },
  { planned: g("Tempo intervals", "Run · Z3"), logged: { state: "pending", title: "Pending", sub: "prescribed today" } },
  { planned: g("Recovery", "Run · Z1"), logged: { state: "upcoming", title: "Upcoming", sub: "not due yet" } },
  { planned: g("Threshold", "Run · Z4"), logged: { state: "upcoming", title: "Upcoming", sub: "not due yet" } },
  { planned: null, logged: { state: "rest", title: "—", sub: "rest day" } },
  { planned: g("Long run", "Run · Z2", { isBenchmark: true }), logged: { state: "upcoming", title: "Upcoming", sub: "not due yet" } },
];
const GUEST_CELLS: DayCell[] = GUEST_CELL_DATA.map((c, i) => ({ ...c, iso: GUEST_ISO[i], day: DOW[i], today: GUEST_ISO[i] === GUEST_TODAY }));

// Sample projection in the live contract's own shape — fatigue, never readiness.
const sampleDay = (i: number, modality: string | null, basis: "prescribed" | "template_estimate", load: number, meanFatigue: number) => ({
  date: GUEST_ISO[i],
  sessions: modality ? [{ planned_session_id: i, modality, basis, load }] : [],
  load,
  mean_fatigue: meanFatigue,
  fatigue: { cns: meanFatigue, muscular: meanFatigue, metabolic: meanFatigue, structural: meanFatigue, tendon: meanFatigue, grip: meanFatigue },
});
const GUEST_PROJECTION: PlannedWeekProjection = {
  available: true,
  reason: null,
  window: { start: GUEST_ISO[2], end: GUEST_ISO[6], block_id: null, week_number: 3 },
  days: [
    sampleDay(2, "running", "prescribed", 330, 38),
    sampleDay(3, "running", "template_estimate", 150, 34),
    sampleDay(4, "running", "template_estimate", 360, 41),
    sampleDay(5, null, "template_estimate", 0, 33),
    sampleDay(6, "running", "template_estimate", 480, 44),
  ],
  peak_mean_fatigue: 44,
};

function GuestPlanningPreview() {
  const { actions } = usePerfLab();

  return (
    <section className="flex flex-col gap-[14px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader title="Planning" subtitle="Planned against logged — each session is dosed against your current readiness and tissue load.">
        <ReadinessChip label="Readiness 64 · sample" known />
        <button onClick={actions.openLog} className={PRIMARY_BTN}>Log workout</button>
      </ScreenHeader>

      {/* whole-surface sample-data label */}
      <div className="rounded-[10px] border border-ac/25 bg-ac/[0.08] px-4 py-3 text-[12px] font-medium leading-[1.5] text-mute">
        <span className="font-semibold text-ac">Preview — sample data.</span> Everything below is a simulated example. Sign in to see your live week, prescription and projection.
      </div>

      <BlockBarView name="Base · Endurance (sample)" weekNumber={3} durationWeeks={7} deloadWeeks={[4]} benchmarkWeeks={[7]} />

      <WeekGrid cells={GUEST_CELLS} todayIso={GUEST_TODAY} summary="sample week · 2 of 6 planned sessions logged" />

      <div className="grid grid-cols-1 items-start gap-[14px] lg:grid-cols-[minmax(0,1fr)_320px]">
        <Card className="p-[22px]">
          <div className="flex items-center justify-between">
            <SectionLabel className={LABEL}>Prescribed session · sample</SectionLabel>
            <div className="font-mono text-[10px] leading-none text-dim">Run · 52 min</div>
          </div>
          <div className="mt-4 text-[22px] font-bold leading-tight text-ink">Tempo intervals — Zone 3</div>
          <div className="mt-2 text-[12.5px] font-medium leading-[1.6] text-mute">
            Knee tissue load (40) caps impact, so volume stays modest. With readiness moderate, intensity is held at threshold-minus to keep CNS cost recoverable before Friday.
          </div>
          <div className="mt-4 flex items-baseline justify-between gap-3 border-t border-white/[0.07] pt-4">
            <span className="text-[13px] font-semibold leading-none text-soft">Tempo repeats</span>
            <span className="font-mono text-[12px] leading-none text-faint">5×6′ · @ 4:30/km · 90s float</span>
          </div>
          <div className="mt-5 border-t border-white/[0.07] pt-4">
            <div className="mb-4 flex items-center justify-between">
              <SectionLabel className={LABEL}>Stress dose · D(t)</SectionLabel>
              <div className="font-mono text-[10px] leading-none text-dim">projected per-session</div>
            </div>
            <div className="flex flex-col gap-3">
              {GUEST_DOSE.map(([name, val, pct, color]) => (
                <MetricBar key={name} label={name} value={val.toFixed(1)} pct={pct} color={color} onClick={() => actions.openExplain(`PD:${name}`)} labelClassName="w-[80px]" valueClassName="w-[30px] text-soft" />
              ))}
            </div>
          </div>
          <div className="mt-[18px] flex gap-[10px]">
            {/* Guest-only surface, so the simulated interval plan is a labelled
                preview rather than a claim about the athlete. */}
            <button onClick={() => actions.openSession(PHASES.map((p) => p.dur))} className={PRIMARY_BTN}>Start session</button>
          </div>
        </Card>

        <div className="flex flex-col gap-[14px]">
          <WeekLoadCard
            sample
            rows={[
              { label: "Sessions", value: "2 / 6", plannedPct: 100, loggedPct: (2 / 6) * 100 },
              { label: "Minutes", value: "114" },
              { label: "Load", value: "526" },
            ]}
          />
          <ProjectionShell sample>
            <ProjectionView data={GUEST_PROJECTION} />
          </ProjectionShell>
        </div>
      </div>
    </section>
  );
}
