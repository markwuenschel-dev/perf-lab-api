// src/perflab/screens/PlanningScreen.tsx
import { useMemo } from "react";
import * as api from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import type { PlannedSessionRead } from "@/types";
import { usePerfLab } from "../store";
import { useAuthedResource } from "../useAuthedResource";
import { Card, MetricBar, ScreenHeader, SectionLabel } from "../ui";
import { Chart, Bars, Line, Marker, Axis, Legend, useVizTheme } from "../viz";
import { COLORS, PLAN_DAYS, PLAN_LOAD, PLAN_READY } from "../sim";

interface WeekCell {
  day: string;
  title: string;
  sub: string;
  subColor: string;
  today?: boolean;
  rest?: boolean;
}

// Prototype week, used as the fallback until the athlete has a real plan block.
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
    };
  });
}

const DOSE: [string, number, number, string][] = [
  ["Volume", 5.5, 55, COLORS.teal],
  ["Intensity", 7.2, 72, "var(--ac)"],
  ["Density", 6.0, 60, "var(--ac)"],
  ["Impact", 4.0, 40, COLORS.warn],
  ["Skill", 2.2, 22, COLORS.good],
  ["Metabolic", 6.8, 68, "var(--ac)"],
];

export function PlanningScreen() {
  const { state, actions } = usePerfLab();
  const auth = useAuth();
  const { accent, colors } = useVizTheme();

  // The displayed Mon–Sun window. Defaults to the current week, but after a block
  // is created it re-derives from that block's start_date (`planningWeekAnchor`),
  // so a block starting outside this week still shows its own first week here.
  const week = useMemo(
    () => weekWindowFor(state.planningWeekAnchor ? parseIsoLocal(state.planningWeekAnchor) : new Date()),
    [state.planningWeekAnchor],
  );
  // `planningRefreshKey` is bumped by BlockCreateModal after a successful
  // POST /v1/planning/blocks so a freshly created block's week shows up here.
  const { data: sessions, loading, error } = useAuthedResource<PlannedSessionRead[]>(
    (t) => api.listPlannedSessions(t, { start_date: week.start_date, end_date: week.end_date }),
    [week.start_date, state.planningRefreshKey],
  );

  // For a signed-in athlete, disambiguate load / error / genuinely-no-block
  // instead of collapsing all three onto "no sessions" (which flickered the
  // empty-state CTA and mislabelled fetch errors as "no block").
  if (auth.token) {
    if (error) return <PlanningNotice title="Couldn't load your plan" body={error} onRetry={() => actions.focusPlanningWeek(week.start_date)} />;
    // `useAuthedResource` first-renders with loading:false before its effect runs,
    // so treat "not yet resolved" (null data, no error) as loading too — otherwise
    // the CTA flashes for one frame.
    if (loading || sessions === null) return <PlanningNotice title="Loading your plan…" body="Fetching this week's prescribed sessions." />;
    if (sessions.length === 0) return <PlanningEmptyState onCreate={actions.openBlockCreate} />;
  }

  // Guests (no token) never hit the branches above and keep the prototype preview.
  const weekCells = buildWeekCells(week.monday, sessions) ?? WEEK;

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader title="Planning" subtitle="Adaptive prescription — each session is dosed against your current readiness and tissue load.">
        <div className="flex items-center gap-[7px] rounded-[9px] border border-ac/25 bg-ac/[0.1] px-[13px] py-[9px] font-mono text-[11px] font-semibold leading-none text-ac">
          <span className="h-[7px] w-[7px] rounded-full bg-ac" />Readiness 64 · holding intensity
        </div>
        {auth.token && (
          <button onClick={actions.openBlockCreate} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft">New block</button>
        )}
      </ScreenHeader>

      {/* week strip */}
      <Card className="px-[18px] pb-4 pt-[18px]">
        <div className="mb-[14px] flex items-center justify-between">
          <SectionLabel>This week</SectionLabel>
          <div className="text-[11px] font-medium leading-none text-dim">Mid-base · wk 3/7</div>
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
            </div>
          ))}
        </div>
      </Card>

      {/* load vs readiness — two stacked small-multiples sharing one Mon–Sun x axis
          (not a dual-axis chart: session load and readiness have different scales). */}
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
        {/* top: session load */}
        <Chart
          width={680}
          height={116}
          padding={{ top: 8, right: 12, bottom: 4, left: 24 }}
          yDomain={[0, 90]}
          ariaLabel="Projected session load, Mon–Sun"
          className="h-[108px] w-full"
        >
          <Bars
            data={PLAN_DAYS.map((d, i) => ({ key: d, label: d, value: PLAN_LOAD[i] }))}
            color="series"
            baseColor={colors.categorical[1]}
            emphasisKey={PLAN_DAYS[2]}
          />
        </Chart>
        {/* bottom: readiness — x-domain [-0.5, 6.5] puts each node at the band center above */}
        <Chart
          width={680}
          height={96}
          padding={{ top: 6, right: 12, bottom: 20, left: 24 }}
          xDomain={[-0.5, 6.5]}
          yDomain={[30, 100]}
          ariaLabel="Projected readiness, Mon–Sun"
          className="h-[96px] w-full"
        >
          <Axis x xLabels={PLAN_DAYS} />
          <Line data={PLAN_READY.map((v, i) => [i, v] as [number, number])} color={accent} width={2.5} />
          {PLAN_READY.map((v, i) => (
            <Marker key={i} x={i} y={v} color={accent} r={3.5} />
          ))}
        </Chart>
      </Card>

      {/* session detail + impact */}
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
              {DOSE.map(([name, val, pct, color]) => (
                <MetricBar key={name} label={name} value={val.toFixed(1)} pct={pct} color={color} onClick={() => actions.openExplain(`PD:${name}`)} labelClassName="w-[80px]" valueClassName="w-[30px] text-soft" />
              ))}
            </div>
          </div>
          <div className="mt-[18px] flex gap-[10px]">
            <button onClick={actions.openSession} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-[18px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Start session</button>
            <button className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft">Swap</button>
          </div>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <SectionLabel className="mb-4">Projected impact</SectionLabel>
            <ImpactRow onClick={() => actions.openExplain("PI:readiness")} label="Readiness after" from="64" to="48" toColor={COLORS.warn} />
            <ImpactRow onClick={() => actions.openExplain("PI:cns")} label="CNS fatigue" from="35" to="52" toColor={COLORS.hot} />
            <ImpactRow onClick={() => actions.openExplain("PI:aerobic")} label="Aerobic drive" from="320" to="+1.4" toColor={COLORS.teal} last />
          </Card>
          <div className="rounded-[18px] border border-ac/[0.18] bg-ac/[0.05] p-[18px]">
            <div className="mb-[10px] flex items-center gap-2 font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.1em] text-ac">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2v6M12 22v-2M5 12H2M22 12h-3" /><circle cx="12" cy="12" r="4" /></svg>Why this session
            </div>
            <div className="text-[12.5px] font-medium leading-[1.6] text-mute">Knee tissue load (40) caps impact, so volume stays modest. With readiness moderate, intensity is held at threshold-minus to keep CNS cost recoverable before Friday.</div>
          </div>
        </div>
      </div>
    </section>
  );
}

// Loading / error placeholder — kept visually distinct from the no-block CTA so a
// fetch that's still in-flight or that errored isn't mistaken for "create a block".
function PlanningNotice({ title, body, onRetry }: { title: string; body: string; onRetry?: () => void }) {
  return (
    <section className="flex min-h-[70vh] items-center justify-center px-[30px] pb-9 pt-[26px]">
      <Card className="flex max-w-[520px] flex-col items-center gap-4 p-[44px] text-center">
        <div className="text-[20px] font-bold leading-[1.2] text-ink">{title}</div>
        <div className="max-w-[380px] text-[13.5px] font-medium leading-[1.6] text-[#7c818c]">{body}</div>
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
        <div className="max-w-[380px] text-[13.5px] font-medium leading-[1.6] text-[#7c818c]">Create a training block to get a week of sessions prescribed against your readiness — pick a goal, cadence and session-length preferences.</div>
        <button onClick={onCreate} className="mt-[6px] rounded-[10px] bg-gradient-to-r from-ac to-[#a7e36e] px-5 py-3 text-[13px] font-semibold leading-none text-[#0a0c10]">Create a training block →</button>
      </Card>
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
