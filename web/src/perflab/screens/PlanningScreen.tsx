// src/perflab/screens/PlanningScreen.tsx
//
// C1a — Planning "honesty pass". The signed-in screen shows only live, backend-
// owned data: the real week strip, canonical readiness, the typed prescription
// detail and its rationale/explanation. The old fixture analytics (projected
// load-vs-readiness chart, per-session stress dose D(t), and projected impact)
// are NOT rendered for authenticated users — they need a backend contract that
// does not exist yet (Fork C0 → C1b, still open). Guests keep the full simulated
// preview, clearly labelled as sample data.
import { useMemo } from "react";
import * as api from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import type { PlannedSessionRead, ReadinessScore, WorkoutPrescription } from "@/types";
import { usePerfLab } from "../store";
import { useAuthedResource } from "../useAuthedResource";
import { assertNever, type AuthedResource } from "../resource";
import { Card, MetricBar, ScreenHeader, SectionLabel, WeakPointTags } from "../ui";
import { WhyThisSession } from "../prescription/WhyThisSession";
import { LoadExplanation } from "../prescription/LoadExplanation";
import { ExpectedOutcomes } from "../prescription/ExpectedOutcomes";
import { PlanRevisionTriggers } from "../prescription/PlanRevisionTriggers";
import { Chart, Bars, Line, Marker, Axis, Legend, useVizTheme } from "../viz";
import { PHASES, PLAN_DAYS, PLAN_LOAD, PLAN_READY } from "../sim";
import { COLORS } from "../readinessPresentation";

interface WeekCell {
  day: string;
  title: string;
  sub: string;
  subColor: string;
  today?: boolean;
  rest?: boolean;
  /** The real planned session behind this cell. Guest fixture cells have none,
   *  which is what keeps the feedback affordance off the preview. */
  sessionId?: number;
  /** Completed or skipped — the only states with an outcome to report on
   *  (ADR-0070). Pending and rescheduled sessions have not happened. */
  terminal?: boolean;
}

// Prototype week — GUEST preview only (signed-in users get buildWeekCells).
const WEEK: WeekCell[] = [
  { day: "Mon", title: "Recovery", sub: "Z1 · 8 km", subColor: "text-good" },
  { day: "Tue", title: "Endurance", sub: "Z2 · 14 km", subColor: "text-teal" },
  { day: "Wed", title: "Tempo intervals", sub: "Z3 · 5×6 min", subColor: "text-ac", today: true },
  { day: "Thu", title: "Recovery", sub: "Z1 · 6 km", subColor: "text-good" },
  { day: "Fri", title: "Threshold", sub: "Z4 · 4×8 min", subColor: "text-warn" },
  { day: "Sat", title: "Rest", sub: "", subColor: "", rest: true },
  { day: "Sun", title: "Long run", sub: "Z2 · 24 km", subColor: "text-teal" },
];

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const isoLocal = (d: Date): string =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const titleCase = (s: string): string => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

// Parse a "YYYY-MM-DD" string as a LOCAL date (new Date(iso) would treat it as UTC
// midnight and can shift the day across timezones).
const parseIsoLocal = (iso: string): Date => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
};

// The Mon–Sun window that contains `date`, plus its ISO bounds for the query.
function weekWindowFor(date: Date): { monday: Date; start_date: string; end_date: string } {
  const monday = new Date(date);
  monday.setDate(date.getDate() - ((date.getDay() + 6) % 7));
  monday.setHours(0, 0, 0, 0);
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  return { monday, start_date: isoLocal(monday), end_date: isoLocal(sunday) };
}

// Map real planned sessions onto a Mon–Sun strip by scheduled date; empty days
// render as rest. Returns null when there's no plan so the caller falls back.
function buildWeekCells(monday: Date, sessions: PlannedSessionRead[] | null): WeekCell[] | null {
  if (!sessions || sessions.length === 0) return null;
  const todayIso = isoLocal(new Date());
  const byDate = new Map(sessions.map((s) => [s.scheduled_date, s]));
  return DOW.map((day, i) => {
    const d = new Date(monday);
    d.setDate(monday.getDate() + i);
    const iso = isoLocal(d);
    const today = iso === todayIso;
    const s = byDate.get(iso);
    if (!s) return { day, title: "Rest", sub: "", subColor: "", rest: true, today };
    return {
      day,
      title: titleCase(s.category || s.modality),
      sub: s.is_deload ? "deload" : titleCase(s.modality),
      subColor: "text-teal",
      today,
      sessionId: s.id,
      terminal: s.status === "completed" || s.status === "skipped",
    };
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
  const goal = state.settings.goal;

  // The displayed Mon–Sun window. Defaults to the current week, but after a block
  // is created it re-derives from that block's start_date (`planningWeekAnchor`).
  const week = useMemo(
    () => weekWindowFor(state.planningWeekAnchor ? parseIsoLocal(state.planningWeekAnchor) : new Date()),
    [state.planningWeekAnchor],
  );
  // `planningRefreshKey` is bumped by BlockCreateModal after a successful
  // POST /v1/planning/blocks so a freshly created block's week shows up here.
  // `feedbackRefreshKey` is bumped after feedback is recorded — this list is what
  // renders the affordance, so it is the resource that has to re-read. Note this
  // re-reads the session LIST, which is a plain GET; re-fetching a prescription
  // would rewrite `prescribed_content`, so it is deliberately not invalidated here.
  const sessions = useAuthedResource<PlannedSessionRead[]>(
    (t) => api.listPlannedSessions(t, { start_date: week.start_date, end_date: week.end_date }),
    [week.start_date, state.planningRefreshKey, state.feedbackRefreshKey],
  );

  // Week-strip load / error / genuinely-no-block distinction (kept from before):
  // a fetch that's in-flight or errored must not be mistaken for "no block". The
  // contract now enforces that ordering rather than this screen re-deriving it —
  // "no block" is read only off a payload that actually loaded.
  switch (sessions.status) {
    // `guest` cannot be observed here: PlanningScreen mounts this body only with
    // a token, and the hook resolves identity from the same auth context in the
    // same commit. It reads as "not resolved yet", which is what it was before.
    case "guest":
    case "loading":
      return <PlanningNotice title="Loading your plan…" body="Fetching this week's prescribed sessions." />;

    case "error":
      return <PlanningNotice title="Couldn't load your plan" body={sessions.error.message} onRetry={() => actions.focusPlanningWeek(week.start_date)} />;

    case "success": {
      if (sessions.data.length === 0) return <PlanningEmptyState onCreate={actions.openBlockCreate} />;

      const weekCells = buildWeekCells(week.monday, sessions.data) ?? [];

      return (
        <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
          <ScreenHeader title="Planning" subtitle="Adaptive prescription — each session is dosed against your current readiness and tissue load.">
            <ReadinessPill />
            <button onClick={actions.openBlockCreate} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft">New block</button>
          </ScreenHeader>

          {/* week strip — live planned sessions */}
          <Card className="px-[18px] pb-4 pt-[18px]">
            <div className="mb-[14px] flex items-center justify-between">
              <SectionLabel>This week</SectionLabel>
            </div>
            <div className="grid grid-cols-7 gap-2">
              {weekCells.map((w) => (
                <div
                  key={w.day}
                  {...(w.today ? {} : { "data-tile": "1" })}
                  className={`flex min-h-[104px] flex-col rounded-[12px] border p-[10px] pt-3 ${w.today ? "border-ac/[0.45] bg-ac/[0.07] shadow-[inset_0_0_0_1px_rgba(198,241,53,.15)]" : "border-white/[0.06]"}`}
                >
                  <div className="flex items-center justify-between">
                    <span className={`font-mono text-[10px] uppercase leading-none ${w.today ? "text-ac" : "text-faint"}`}>{w.day}</span>
                    {w.today && <span className="rounded-[5px] bg-ac px-[5px] py-[3px] font-mono text-[8px] leading-none text-[#0a0c10]">TODAY</span>}
                  </div>
                  <div className={`mt-auto text-[12px] leading-[1.3] ${w.today ? "font-bold text-ink" : w.rest ? "font-semibold text-faint" : "font-semibold text-mute"}`}>{w.title}</div>
                  {w.sub && <div className={`mt-1 text-[10px] font-medium leading-none ${w.subColor}`}>{w.sub}</div>}
                  {/* Feedback needs a session that actually has an outcome. The
                      id comes from this cell's own row, never from whatever the
                      prescription card happens to be showing — those are separate
                      resources and can target different sessions. */}
                  {w.terminal && w.sessionId != null && (
                    <button
                      onClick={() => actions.openFeedback(w.sessionId!)}
                      className="mt-2 rounded-[7px] border border-white/10 bg-white/[0.04] px-2 py-[5px] text-[10px] font-semibold leading-none text-soft"
                    >
                      Feedback
                    </button>
                  )}
                </div>
              ))}
            </div>
          </Card>

          {/* live prescribed session + its rationale/explanation */}
          <PrescribedSessionCard goal={goal} />
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

// The prescribed session, from the same live seam TwinScreen uses
// (GET /v1/next-session → typed WorkoutPrescription). A prescription failure is
// localized to this card and never restores fixtures.
function PrescribedSessionCard({ goal }: { goal: string }) {
  const prescription = useAuthedResource<WorkoutPrescription>((t) => api.getNextSession(goal, t), [goal]);

  // The card shell and its header render in every state — only the interior and
  // the header's right-hand summary are state-dependent — so this surface reads
  // the contract through exhaustive switches rather than <ResourceState>, which
  // replaces a whole card/section with a notice.
  return (
    <Card className="p-[22px]">
      <div className="flex items-center justify-between">
        <SectionLabel>Prescribed session</SectionLabel>
        <div className="font-mono text-[10px] leading-none text-dim">{prescriptionSummary(prescription)}</div>
      </div>

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
    // deliberately silent, as it was before: a guest is told nothing here.
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
            <div className="flex flex-col gap-[10px] border-t border-white/[0.06] pt-4">
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
          <button onClick={onRetry} className="mt-[6px] rounded-[10px] border border-white/10 bg-white/[0.04] px-5 py-3 text-[13px] font-semibold leading-none text-soft">Retry</button>
        )}
      </Card>
    </section>
  );
}

// Replaces the dead-end where a fresh signed-in athlete with no block silently
// saw the hard-coded prototype WEEK and had no way to get a real one.
function PlanningEmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <section className="flex min-h-[70vh] items-center justify-center px-[30px] pb-9 pt-[26px]">
      <Card className="flex max-w-[520px] flex-col items-center gap-4 p-[44px] text-center">
        <div className="grid h-[60px] w-[60px] place-items-center rounded-[16px] border border-ac/25 bg-ac/[0.1]">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="1.6"><path d="M12 2 4 7v10l8 5 8-5V7z" /><path d="M12 22V12M4 7l8 5 8-5" /></svg>
        </div>
        <div className="text-[22px] font-bold leading-[1.2] text-ink">No active training block</div>
        <div className="max-w-[380px] text-[13.5px] font-medium leading-[1.6] text-mute">Create a training block to get a week of sessions prescribed against your readiness — pick a goal, cadence and session-length preferences.</div>
        <button onClick={onCreate} className="mt-[6px] rounded-[10px] bg-gradient-to-r from-ac to-[#a7e36e] px-5 py-3 text-[13px] font-semibold leading-none text-[#0a0c10]">Create a training block →</button>
      </Card>
    </section>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Guest: the full simulated preview, labelled as sample data end-to-end.
// This is the *only* place the projected chart, stress-dose D(t) and projected-
// impact fixtures still render — authenticated users never see them.
// ──────────────────────────────────────────────────────────────────────────
const GUEST_DOSE: [string, number, number, string][] = [
  ["Volume", 5.5, 55, COLORS.teal],
  ["Intensity", 7.2, 72, "var(--ac)"],
  ["Density", 6.0, 60, "var(--ac)"],
  ["Impact", 4.0, 40, COLORS.warn],
  ["Skill", 2.2, 22, COLORS.good],
  ["Metabolic", 6.8, 68, "var(--ac)"],
];

function GuestPlanningPreview() {
  const { actions } = usePerfLab();
  const { accent, colors } = useVizTheme();

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader title="Planning" subtitle="Adaptive prescription — each session is dosed against your current readiness and tissue load.">
        <div className="flex items-center gap-[7px] rounded-[9px] border border-ac/25 bg-ac/[0.1] px-[13px] py-[9px] font-mono text-[11px] font-semibold leading-none text-ac">
          <span className="h-[7px] w-[7px] rounded-full bg-ac" />Readiness 64 · sample
        </div>
      </ScreenHeader>

      {/* whole-surface sample-data label */}
      <div className="rounded-[10px] border border-ac/25 bg-ac/[0.08] px-4 py-3 text-[12px] font-medium leading-[1.5] text-mute">
        <span className="font-semibold text-ac">Preview — sample data.</span> Everything below is a simulated example. Sign in to see your live week, prescription and rationale.
      </div>

      {/* week strip (sample) */}
      <Card className="px-[18px] pb-4 pt-[18px]">
        <div className="mb-[14px] flex items-center justify-between">
          <SectionLabel>This week</SectionLabel>
          <div className="text-[11px] font-medium leading-none text-dim">Mid-base · wk 3/7</div>
        </div>
        <div className="grid grid-cols-7 gap-2">
          {WEEK.map((w) => (
            <div
              key={w.day}
              {...(w.today ? {} : { "data-tile": "1" })}
              className={`flex min-h-[104px] flex-col rounded-[12px] border p-[10px] pt-3 ${w.today ? "border-ac/[0.45] bg-ac/[0.07] shadow-[inset_0_0_0_1px_rgba(198,241,53,.15)]" : "border-white/[0.06]"}`}
            >
              <div className="flex items-center justify-between">
                <span className={`font-mono text-[10px] uppercase leading-none ${w.today ? "text-ac" : "text-faint"}`}>{w.day}</span>
                {w.today && <span className="rounded-[5px] bg-ac px-[5px] py-[3px] font-mono text-[8px] leading-none text-[#0a0c10]">TODAY</span>}
              </div>
              <div className={`mt-auto text-[12px] leading-[1.3] ${w.today ? "font-bold text-ink" : w.rest ? "font-semibold text-faint" : "font-semibold text-mute"}`}>{w.title}</div>
              {w.sub && <div className={`mt-1 text-[10px] font-medium leading-none ${w.subColor}`}>{w.sub}</div>}
            </div>
          ))}
        </div>
      </Card>

      {/* load vs readiness (sample) — two stacked small-multiples sharing one
          Mon–Sun x axis (session load and readiness have different scales). */}
      <Card className="px-[22px] py-5">
        <div className="mb-2 flex items-center justify-between">
          <SectionLabel>Projected load vs readiness</SectionLabel>
          <Legend
            items={[
              { label: "session load", color: colors.categorical[1], mark: "rect" },
              { label: "readiness", color: accent, mark: "line" },
            ]}
          />
        </div>
        <Chart
          width={680}
          height={116}
          padding={{ top: 8, right: 12, bottom: 4, left: 24 }}
          yDomain={[0, 90]}
          ariaLabel="Sample projected session load, Mon–Sun"
          className="h-[108px] w-full"
        >
          <Bars
            data={PLAN_DAYS.map((d, i) => ({ key: d, label: d, value: PLAN_LOAD[i] }))}
            color="series"
            baseColor={colors.categorical[1]}
            emphasisKey={PLAN_DAYS[2]}
          />
        </Chart>
        <Chart
          width={680}
          height={96}
          padding={{ top: 6, right: 12, bottom: 20, left: 24 }}
          xDomain={[-0.5, 6.5]}
          yDomain={[30, 100]}
          ariaLabel="Sample projected readiness, Mon–Sun"
          className="h-[96px] w-full"
        >
          <Axis x xLabels={PLAN_DAYS} />
          <Line data={PLAN_READY.map((v, i) => [i, v] as [number, number])} color={accent} width={2.5} />
          {PLAN_READY.map((v, i) => (
            <Marker key={i} x={i} y={v} color={accent} r={3.5} />
          ))}
        </Chart>
      </Card>

      {/* session detail + impact (sample) */}
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[1fr_360px]">
        <Card className="p-[22px]">
          <div className="flex items-center justify-between">
            <div>
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-ac">Wednesday · prescribed</div>
              <div className="mt-[9px] text-[22px] font-bold leading-none text-ink">Tempo intervals — Zone 3</div>
            </div>
            <div className="text-right">
              <div className="font-mono text-[26px] font-semibold leading-none text-ink">5×6′</div>
              <div className="mt-[5px] text-[11px] font-medium leading-none text-faint">@ 4:30/km · 90s float</div>
            </div>
          </div>
          <div className="mt-5 border-t border-white/[0.06] pt-[18px]">
            <div className="mb-4 flex items-center justify-between">
              <SectionLabel>Stress dose · D(t)</SectionLabel>
              <div className="font-mono text-[10px] leading-none text-dim">projected per-session</div>
            </div>
            <div className="flex flex-col gap-3">
              {GUEST_DOSE.map(([name, val, pct, color]) => (
                <MetricBar key={name} label={name} value={val.toFixed(1)} pct={pct} color={color} onClick={() => actions.openExplain(`PD:${name}`)} labelClassName="w-[80px]" valueClassName="w-[30px] text-soft" />
              ))}
            </div>
          </div>
          <div className="mt-[18px] flex gap-[10px]">
            {/* Guest-only surface (PlanningScreen:87), so the simulated interval
                plan is a labelled preview rather than a claim about the athlete. */}
            <button onClick={() => actions.openSession(PHASES.map((p) => p.dur))} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-[18px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Start session</button>
          </div>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <SectionLabel className="mb-4">Projected impact</SectionLabel>
            <ImpactRow onClick={() => actions.openExplain("PI:readiness")} label="Readiness after" from="64" to="48" toColor={COLORS.warn} />
            <ImpactRow onClick={() => actions.openExplain("PI:cns")} label="CNS fatigue" from="35" to="52" toColor={COLORS.hot} />
            <ImpactRow onClick={() => actions.openExplain("PI:aerobic")} label="Aerobic drive" from="320" to="+1.4" toColor={COLORS.teal} last />
          </Card>
          <Card className="border-ac/[0.18] bg-ac/[0.05] p-[18px]">
            <div className="mb-[10px] flex items-center gap-2 font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.1em] text-ac">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2v6M12 22v-2M5 12H2M22 12h-3" /><circle cx="12" cy="12" r="4" /></svg>Why this session
            </div>
            <div className="text-[12.5px] font-medium leading-[1.6] text-mute">Knee tissue load (40) caps impact, so volume stays modest. With readiness moderate, intensity is held at threshold-minus to keep CNS cost recoverable before Friday.</div>
          </Card>
        </div>
      </div>
    </section>
  );
}

function ImpactRow({ label, from, to, toColor, onClick, last }: { label: string; from: string; to: string; toColor: string; onClick: () => void; last?: boolean }) {
  return (
    <div onClick={onClick} className={`flex cursor-pointer items-center justify-between ${last ? "" : "mb-[14px]"}`}>
      <span className="text-[12px] font-medium leading-none text-mute">{label}</span>
      <div className="flex items-center gap-2">
        <span className="font-mono text-[16px] font-semibold leading-none text-soft">{from}</span>
        <span className="text-[12px] font-medium leading-none text-faint">→</span>
        <span className="font-mono text-[16px] font-semibold leading-none" style={{ color: toColor }}>{to}</span>
      </div>
    </div>
  );
}
