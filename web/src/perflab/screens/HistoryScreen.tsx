// src/perflab/screens/HistoryScreen.tsx
import { useState, type ReactNode } from "react";
import * as api from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import { cn } from "@/lib/utils";
import type { BenchmarkObservationRead, StateHistorySnapshotRead, WellnessSampleOut, WorkoutLogSummary } from "@/types";
import { usePerfLab } from "../store";
import { useAuthedResource } from "../useAuthedResource";
import { ResourceState } from "../ResourceState";
import { resourceData, type AuthedResource } from "../resource";
import { Card, ScreenHeader, SectionLabel, Track } from "../ui";
import { Chart, Area, Axis, Bars, TableView, useChart, useVizTheme } from "../viz";
import { aerobicValue, fatigueDisplayProxy } from "../stateVector";
import { dispositionTag } from "../workoutDisposition";
import { AEROBIC_CEILING, RANGES, RANGE_WEEKS, filterHistoryWindow, weeklyLoad, type Range } from "./historyData";

/** Compact load formatter — real volume-load totals run large, so thousands
 *  collapse to "12.4k". */
// Console section label (mono 10px, faint) — a local override of the shared
// SectionLabel default, which the not-yet-migrated screens still render at 11px.
const LABEL = "text-[10px] text-faint";

function fmtLoad(v: number): string {
  if (!Number.isFinite(v)) return "—";
  return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${Math.round(v)}`;
}

/** Acute:chronic workload ratio band. 0.8–1.3 is a common training-load rule of
 *  thumb, not a validated injury-risk predictor: the labels say where the ratio
 *  sits, never a risk the engine has measured. */
function acwrBand(r: number): { label: string; color: string } {
  if (r < 0.8) return { label: "Low", color: "var(--color-faint)" };
  if (r <= 1.3) return { label: "In range", color: "var(--color-good)" };
  if (r <= 1.5) return { label: "Ramping", color: "var(--color-warn)" };
  return { label: "Spike", color: "var(--color-hot)" };
}

/** Map a ratio onto the gauge's 0.5–2.0 visual scale (band edges 0.8/1.3/1.5
 *  land at 20% / 53.3% / 66.7%). */
const acwrPos = (r: number): number => Math.max(2, Math.min(98, ((r - 0.5) / 1.5) * 100));

// Load balance (ACWR) — reads the same weekly buckets the chart draws, so the
// gauge literally reflects the bars beside it: acute = this week, chronic = the
// trailing 4-week (28d) average. The band is a rule of thumb, not a safety check.
function LoadBalanceCard({ acwr, acute, chronic }: { acwr: number | null; acute: number; chronic: number }) {
  const band = acwr != null ? acwrBand(acwr) : null;
  return (
    <Card className="px-[22px] py-5">
      <div className="mb-3 flex items-center justify-between">
        <SectionLabel className={LABEL}>Load balance</SectionLabel>
        <div className="text-[11px] font-medium leading-none text-dim">acute : chronic</div>
      </div>
      {acwr == null || band == null ? (
        <>
          <div className="font-mono text-[34px] font-semibold leading-none text-dim">—</div>
          <p className="mt-3 text-[12px] font-medium leading-[1.5] text-mute">
            Log a few more weeks of training to gauge your acute-to-chronic load balance.
          </p>
        </>
      ) : (
        <>
          <div className="flex items-end gap-[10px]">
            <span className="font-mono text-[34px] font-semibold leading-none text-ink">{acwr.toFixed(2)}</span>
            <span
              className="mb-[5px] rounded-full px-[9px] py-[4px] text-[10px] font-bold uppercase leading-none tracking-[0.08em]"
              style={{
                color: band.color,
                background: `color-mix(in srgb, ${band.color} 13%, transparent)`,
                border: `1px solid color-mix(in srgb, ${band.color} 30%, transparent)`,
              }}
            >
              {band.label}
            </span>
          </div>
          <div className="mt-2 font-mono text-[11px] leading-none text-mute">
            acute 7d · {fmtLoad(acute)} &nbsp;·&nbsp; chronic 28d · {fmtLoad(chronic)}
          </div>
          <div className="relative mt-4">
            <div
              className="h-[9px] rounded-[6px]"
              style={{ background: "linear-gradient(90deg,var(--color-dim) 0 20%,var(--color-good) 20% 53.3%,var(--color-warn) 53.3% 66.7%,var(--color-hot) 66.7% 100%)" }}
            />
            <div
              className="absolute top-[-2px] h-[13px] w-[2px] rounded-[2px] bg-ink"
              style={{ left: `${acwrPos(acwr)}%`, boxShadow: "0 0 0 2px var(--color-tile)" }}
            />
          </div>
          <div className="relative mt-[7px] h-[10px] font-mono text-[8.5px] text-dim">
            <span className="absolute -translate-x-1/2" style={{ left: "20%" }}>0.8</span>
            <span className="absolute -translate-x-1/2" style={{ left: "53.3%" }}>1.3</span>
            <span className="absolute -translate-x-1/2" style={{ left: "66.7%" }}>1.5</span>
          </div>
          <p className="mt-4 text-[11px] font-medium leading-[1.45] text-faint">
            The 7-day vs 28-day workload ratio. The bands are a common rule of thumb, not a validated injury predictor.
          </p>
        </>
      )}
    </Card>
  );
}

/** Clickable day dots over the readiness chart — each time-travels the twin. */
function DayMarkers({ readiness, onPick, color }: { readiness: number[]; onPick: (i: number) => void; color: string }) {
  const { xScale, yScale } = useChart();
  if (!xScale || !yScale) return null;
  return (
    <g>
      {readiness.map((r, i) => (
        <g key={i}>
          <circle cx={xScale(i)} cy={yScale(r)} r={2.5} fill={color} style={{ pointerEvents: "none" }} />
          <circle cx={xScale(i)} cy={yScale(r)} r={10} fill="transparent" onClick={() => onPick(i)} className="cursor-pointer" />
        </g>
      ))}
    </g>
  );
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const FIELD_TEST_CODE = "run_vo2_field_test_300m_1p5mi";
const fmtDay = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${d.getDate()} ${MONTHS[d.getMonth()]}`;
};
const fmtMonth = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : MONTHS[d.getMonth()];
};
const cell = (v: number | null, suffix = ""): string => (v == null ? "—" : `${v}${suffix}`);

// Wellness rows carry the source that reported them. Only "manual" has a fixed meaning
// (the athlete typed it); every other value is a provider name, so it is humanised
// rather than mapped — a new integration must not need an edit here to render.
const sourceLabel = (s: string | null | undefined): string =>
  s == null || s.trim() === "" ? "—" : s.trim().toLowerCase() === "manual" ? "You" : s.replace(/_/g, " ");

// VO₂max progression — derived from the athlete's real field-test benchmark
// observations (the same series the field-test log below shows), newest first.
// No signed-in athlete sees a fabricated curve: guests and empty histories get
// an honest prompt instead — and, since the card now reads the canonical
// resource union, those two are no longer the same branch. It shares `obsRes`
// with the field-test log below, so it deliberately shares that card's copy for
// a failed load rather than inventing a second wording for one failure.
function VO2maxCard({ resource }: { resource: AuthedResource<BenchmarkObservationRead[]> }) {
  const shell = (inner: ReactNode) => (
    <Card className="border-mint/[0.18] p-[18px]" style={{ background: "linear-gradient(120deg,#0f1f1c,var(--color-tile) 60%)" }}>
      <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#9ad6c8]">VO₂max progression</div>
      {inner}
    </Card>
  );
  return shell(
    <ResourceState
      resource={resource}
      isEmpty={(rows) => rows.length === 0}
      variant="note"
      className="mt-3"
      guest={{ body: "Sign in and log a field test to track your VO₂max." }}
      loadingContent={{ body: "Loading field tests…" }}
      error={{ body: "Couldn't load your field tests — try again." }}
      empty={{ body: "No field tests logged yet — record one in Assess." }}
      staleLabel="Couldn't refresh your field tests — showing your last loaded results."
    >
      {(obs) => {
        const latest = obs[0].raw_value;
        const spark = obs.slice(0, 4).reverse(); // oldest→newest of the last few
        const first = spark[0];
        const delta = spark.length >= 2 ? latest - first.raw_value : null;
        return (
          <>
            <div className="mt-3 flex items-end gap-2">
              <span className="font-mono text-[28px] font-semibold leading-none text-ink">{latest.toFixed(1)}</span>
              {delta != null && (
                <span className={`mb-1 text-[11px] font-medium leading-none ${delta >= 0 ? "text-good" : "text-hot"}`}>
                  {delta >= 0 ? "+" : "−"}{Math.abs(delta).toFixed(1)} since {fmtMonth(first.observed_at)}
                </span>
              )}
            </div>
            <div className="mt-3 font-mono text-[11px] leading-none text-mute">
              {spark.map((o) => o.raw_value.toFixed(1)).join(" → ")}
            </div>
          </>
        );
      }}
    </ResourceState>,
  );
}

// Aerobic capacity — the decomposed aerobic capacity axis from the latest real
// snapshot, compared against the oldest snapshot currently loaded (the window
// start, not a durable "base"). Track fills against the twin's aerobic ceiling.
//
// It renders the SAME history resource the readiness chart does, so it takes the
// resource for branch selection and the already-windowed endpoints as props —
// one window derivation (one clock read) feeds both, and the two cards can never
// disagree about which snapshots are in view.
function AerobicCapacityCard({ resource, latest, windowStart }: {
  resource: AuthedResource<StateHistorySnapshotRead[]>;
  latest: StateHistorySnapshotRead | null;
  windowStart: StateHistorySnapshotRead | null;
}) {
  const shell = (inner: ReactNode) => (
    <Card className="p-[18px]">
      <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">Aerobic capacity</div>
      {inner}
    </Card>
  );
  return shell(
    <ResourceState
      resource={resource}
      // "Empty" here is the editorial reading of a successful history: rows
      // exist, but none inside the selected window.
      isEmpty={() => latest == null}
      variant="note"
      className="mt-3"
      guest={{ body: "Sign in to track your aerobic capacity." }}
      loadingContent={{ body: "Loading history…" }}
      error={{ body: "Couldn't load your history — try again." }}
      empty={{ body: "Not enough history yet — log training to build the trend." }}
      staleLabel="Couldn't refresh your history — showing your last loaded capacity."
    >
      {() => {
        // `isEmpty` already routed a null `latest` to the empty branch; this
        // guard is only what TypeScript needs to narrow the prop.
        if (!latest) return null;
        const val = aerobicValue(latest);
        const delta = windowStart ? val - aerobicValue(windowStart) : null;
        const pct = Math.max(0, Math.min(100, (val / AEROBIC_CEILING) * 100));
        return (
          <>
            <div className="mt-3 flex items-end gap-2">
              <span className="font-mono text-[28px] font-semibold leading-none text-ink">{Math.round(val)}</span>
              {delta != null && (
                <span className={`mb-1 text-[11px] font-medium leading-none ${delta >= 0 ? "text-good" : "text-hot"}`}>
                  {delta >= 0 ? "+" : "−"}{Math.abs(Math.round(delta))} vs window start
                </span>
              )}
            </div>
            <Track pct={pct} background="linear-gradient(90deg,var(--ac),#a7e36e)" className="mt-3 h-[6px]" />
            <div className="mt-2 font-mono text-[10px] leading-none text-dim">of {AEROBIC_CEILING} axis ceiling</div>
          </>
        );
      }}
    </ResourceState>,
  );
}

// Recent wellness — real daily samples from GET /v1/wellness. Renders live rows
// when the athlete has logged check-ins; guests and empty histories get a note
// rather than a mock series, and they are now distinct branches rather than one
// "no data" fallthrough.
function RecentWellnessCard() {
  const wellnessRes = useAuthedResource<WellnessSampleOut[]>((t) => api.listWellness(t, 10), []);

  return (
    <Card className="px-[22px] py-5">
      <SectionLabel className={cn(LABEL, "mb-2")}>Recent wellness</SectionLabel>
      <ResourceState
        resource={wellnessRes}
        isEmpty={(rows) => rows.length === 0}
        variant="note"
        guest={{ body: "Sign in and log a check-in to track your wellness here." }}
        loadingContent={{ body: "Loading recent samples…" }}
        error={{ body: "Couldn't load your wellness — try again." }}
        empty={{ body: "No wellness logged yet — use Check-in to record sleep, HRV and resting HR." }}
        staleLabel="Couldn't refresh your wellness — showing your last loaded samples."
      >
        {(samples) => (
          <>
            <div className="grid grid-cols-[1.1fr_1fr_1fr_1fr_1fr] gap-2 border-b border-white/[0.07] py-[10px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.1em] text-dim">
              <span>Date</span><span>HRV</span><span>Sleep</span><span>RHR</span><span>Mood</span>
            </div>
            {samples.map((w) => (
              <div key={w.id} className="grid grid-cols-[1.1fr_1fr_1fr_1fr_1fr] items-center gap-2 border-b border-white/[0.07] py-[12px] last:border-0">
                {/* A day can hold several rows — the table is keyed (user, date, source) —
                    so the date alone renders two different sources as two identical rows.
                    The source is what tells them apart. */}
                <span className="flex flex-col gap-[3px]">
                  <span className="text-[13px] font-semibold leading-none text-ink">{fmtDay(w.date)}</span>
                  <span className="font-mono text-[10px] font-medium leading-none tracking-[0.06em] text-faint">
                    {sourceLabel(w.source)}
                  </span>
                </span>
                <span className="font-mono text-[13px] font-medium leading-none text-soft">{cell(w.hrv_ms, " ms")}</span>
                <span className="font-mono text-[13px] font-medium leading-none text-soft">{cell(w.sleep_hours, " h")}</span>
                <span className="font-mono text-[13px] font-medium leading-none text-soft">{cell(w.resting_hr)}</span>
                <span className="font-mono text-[13px] font-medium leading-none text-soft">{cell(w.mood)}</span>
              </div>
            ))}
          </>
        )}
      </ResourceState>
    </Card>
  );
}

export function FieldTestLogCard({ resource }: { resource: AuthedResource<BenchmarkObservationRead[]> }) {
  return (
    <Card className="px-[22px] py-5">
      <SectionLabel className={cn(LABEL, "mb-2")}>Field test log</SectionLabel>
      <ResourceState
        resource={resource}
        isEmpty={(rows) => rows.length === 0}
        variant="note"
        guest={{ body: "Sign in and log a field test to see your results here." }}
        loadingContent={{ body: "Loading field tests…" }}
        error={{ body: "Couldn't load your field tests — try again." }}
        empty={{ body: "No field tests logged yet — record one in Assess." }}
        staleLabel="Couldn't refresh your field tests — showing your last loaded results."
      >
        {(observations) => (
          <TableView
            columns={[
              { key: "date", label: "Date" },
              { key: "vo2", label: "VO₂max", numeric: true },
              { key: "aerobicScore", label: "Aerobic score", numeric: true },
              { key: "validity", label: "Validity" },
            ]}
            rows={observations.map((observation, index) => ({
              date: (
                <span className="font-semibold text-ink">
                  {fmtDay(observation.observed_at)}
                  {index === 0 && <span className="ml-[6px] text-[10px] font-medium text-ac">latest</span>}
                </span>
              ),
              vo2: (
                <span className={index === 0 ? "font-semibold text-teal" : "font-semibold"}>
                  {observation.raw_value.toFixed(1)}
                </span>
              ),
              aerobicScore: cell(
                observation.normalized_value == null ? null : Math.round(observation.normalized_value),
                "/100",
              ),
              // P3-pre: a field test dated before the model's latest update is kept, but did
              // not update the training state; say so beside its validity.
              validity: [observation.validity_status, dispositionTag(observation.state_disposition)]
                .filter(Boolean)
                .join(" · "),
            }))}
          />
        )}
      </ResourceState>
    </Card>
  );
}

export function HistoryScreen() {
  const { state, actions } = usePerfLab();
  const { token } = useAuth();
  const { accent, colors } = useVizTheme();
  const [range, setRange] = useState<Range>("12w");

  // Real trends only — signed-in athletes never see a fabricated series. A large
  // history fetch backs the "All" window; the toggle filters client-side.
  const historyRes = useAuthedResource<StateHistorySnapshotRead[]>((t) => api.getStateHistory(t, 365), [state.workoutsRefreshKey]);
  const workoutsRes = useAuthedResource<WorkoutLogSummary[]>((t) => api.listWorkouts(t, 300), [state.workoutsRefreshKey]);
  const obsRes = useAuthedResource<BenchmarkObservationRead[]>(
    (t) => api.listBenchmarkObservations(t, { benchmarkCode: FIELD_TEST_CODE, limit: 10 }),
    [],
  );

  const weeks = RANGE_WEEKS[range];

  // Readiness history, filtered to the selected window (real data only).
  // `resourceData` yields rows on the success branch only, so a guest, a first
  // load and a failed load all derive to "nothing to plot" — and the branch that
  // says WHICH of those it is stays with <ResourceState> below, never re-derived
  // from a null payload here.
  const historyRows = resourceData(historyRes);
  const visibleHistory = historyRows ? filterHistoryWindow(historyRows, weeks) : [];
  const readinessSeries = visibleHistory.map(fatigueDisplayProxy);
  const N = readinessSeries.length;
  const latestReadiness = N > 0 ? readinessSeries[N - 1] : null;
  const hDiff = N >= 2 ? readinessSeries[N - 1] - readinessSeries[0] : null;
  const hDelta = hDiff == null ? "" : `${hDiff >= 0 ? "+" : "−"}${Math.abs(hDiff)} vs window start`;
  const latestSnap = N > 0 ? visibleHistory[N - 1] : null;
  const windowStartSnap = N > 0 ? visibleHistory[0] : null;

  // Weekly load over the same window (real workouts only; null → empty state).
  const realLoad = weeklyLoad(resourceData(workoutsRes), weeks);
  const loadSeries = realLoad ?? [];
  const loadMax = Math.max(1, ...loadSeries) * 1.1;
  const nowIdx = loadSeries.length - 1;

  // Weekly-load detail strip: this week vs last, block average, and the peak week.
  const thisWeek = loadSeries[nowIdx] ?? 0;
  const lastWeek = loadSeries.length >= 2 ? loadSeries[nowIdx - 1] : null;
  const avgLoad = loadSeries.length ? loadSeries.reduce((a, b) => a + b, 0) / loadSeries.length : 0;
  const peakVal = loadSeries.length ? Math.max(0, ...loadSeries) : 0;
  const peakWk = loadSeries.indexOf(peakVal) + 1;
  const wowDelta = lastWeek && lastWeek > 0 ? (thisWeek - lastWeek) / lastWeek : null;

  // Acute:chronic balance from the same buckets — acute = current week, chronic =
  // trailing 4-week (28d) mean. Needs ≥2 weeks of real load or it's not meaningful.
  const recent4 = loadSeries.slice(-4);
  const chronic = recent4.length ? recent4.reduce((a, b) => a + b, 0) / recent4.length : 0;
  const acwr = chronic > 0 && recent4.filter((v) => v > 0).length >= 2 ? thisWeek / chronic : null;

  // Time-travel the twin from a clicked readiness dot — hand it the CLICKED ROW's
  // snapshot_id (its durable identity), never the chart index.
  const goDay = (i: number) => {
    const picked = visibleHistory[i];
    if (picked) {
      actions.setSelectedTwinSnapshot(picked.snapshot_id);
      actions.setScreen("twin");
    }
  };

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader title="History" subtitle="How your twin and assessments have moved over your recent training.">
        {/* Check-in writes wellness: signed-in athletes only, as on the other console screens. */}
        {token != null && (
          <button onClick={actions.openCheckin} className="rounded-[9px] border border-white/[0.07] bg-white/[0.04] px-[14px] py-[9px] text-[12.5px] font-semibold leading-none text-soft">
            Check in
          </button>
        )}
        <button onClick={actions.openLog} className="rounded-[9px] bg-ac px-[15px] py-[9px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Log workout</button>
      </ScreenHeader>

      <div className="flex flex-col gap-4">
        <div className="flex justify-end">
          <div role="group" aria-label="Range" className="flex gap-[7px] rounded-[9px] border border-white/[0.07] p-[3px]">
            {RANGES.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setRange(t)}
                aria-pressed={t === range}
                className={cn(
                  "cursor-pointer rounded-[7px] px-[11px] py-[7px] text-[11px] font-semibold leading-none transition-colors",
                  t === range ? "bg-ac text-[#0a0c10] shadow-[0_6px_18px_-8px_var(--ac)]" : "bg-white/[0.04] text-soft hover:bg-white/[0.06]",
                )}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_300px]">
          <Card className="px-[22px] py-5">
            <div className="mb-2 flex items-start justify-between">
              <div>
                <SectionLabel className={LABEL}>Readiness</SectionLabel>
                <div className="mt-2 flex items-end gap-2">
                  <span className="font-mono text-[30px] font-semibold leading-none text-ink">{latestReadiness ?? "—"}</span>
                  {hDelta && <span className={`mb-1 text-[11px] font-medium leading-none ${hDiff != null && hDiff >= 0 ? "text-good" : "text-hot"}`}>{hDelta}</span>}
                </div>
              </div>
              <div className="text-right font-mono text-[10px] leading-none text-dim">click a point to time-travel</div>
            </div>
            <ResourceState
              resource={historyRes}
              // A single point cannot draw a trend, so it reads as "not enough
              // history" — an editorial reading of successful data, which is
              // exactly what `isEmpty` is for. It closes over `N` rather than
              // re-filtering so the chart and the header above it always agree.
              isEmpty={() => N < 2}
              variant="note"
              className="flex h-[170px] items-center justify-center"
              guest={{ body: "Sign in to see your readiness trend." }}
              loadingContent={{ body: "Loading history…" }}
              error={{ body: "Couldn't load your history — try again." }}
              empty={{ body: "Not enough history in this window yet — log training or widen the range." }}
              staleLabel="Couldn't refresh your history — showing your last loaded trend."
            >
              {() => (
                <Chart
                  width={600}
                  height={180}
                  padding={{ top: 14, right: 10, bottom: 15, left: 10 }}
                  xDomain={[0, N - 1]}
                  yDomain={[20, 100]}
                  ariaLabel="Readiness across the selected window"
                  className="mt-1 h-[170px] w-full"
                >
                  <Axis y yTicks={3} />
                  <Area data={readinessSeries.map((r, i) => [i, r] as [number, number])} color={accent} fillOpacity={0.14} />
                  <DayMarkers readiness={readinessSeries} onPick={goDay} color={accent} />
                </Chart>
              )}
            </ResourceState>
          </Card>
          <div className="flex flex-col gap-4">
            <VO2maxCard resource={obsRes} />
            <AerobicCapacityCard resource={historyRes} latest={latestSnap} windowStart={windowStartSnap} />
          </div>
        </div>

        <RecentWellnessCard />

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
          <Card className="px-[22px] py-5">
            <div className="mb-4 flex items-center justify-between">
              <SectionLabel className={LABEL}>Weekly training load</SectionLabel>
              <div className="text-[11px] font-medium leading-none text-dim">volume load · last {weeks} weeks</div>
            </div>
            <ResourceState
              resource={workoutsRes}
              // Logged workouts that all fall outside the selected window are a
              // successful-but-empty read, not a failed one.
              isEmpty={() => loadSeries.length === 0}
              variant="note"
              className="flex h-[140px] items-center justify-center"
              guest={{ body: "Sign in to see your training load." }}
              loadingContent={{ body: "Loading training load…" }}
              error={{ body: "Couldn't load your training load — try again." }}
              empty={{ body: "No training logged in this window — log a session or widen the range." }}
              staleLabel="Couldn't refresh your training load — showing your last loaded weeks."
            >
              {() => (
                <>
                  <div className="mb-4 flex flex-wrap gap-x-7 gap-y-3">
                    <div>
                      <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">This week</div>
                      <div className="mt-[6px] font-mono text-[22px] font-semibold leading-none text-ink">{fmtLoad(thisWeek)}</div>
                    </div>
                    <div>
                      <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">vs last wk</div>
                      <div className={`mt-[6px] font-mono text-[22px] font-semibold leading-none ${wowDelta == null ? "text-dim" : wowDelta >= 0 ? "text-good" : "text-hot"}`}>
                        {wowDelta == null ? "—" : `${wowDelta >= 0 ? "+" : "−"}${Math.abs(wowDelta * 100).toFixed(0)}%`}
                      </div>
                    </div>
                    <div>
                      <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">{weeks}-wk avg</div>
                      <div className="mt-[6px] font-mono text-[22px] font-semibold leading-none text-ink">{fmtLoad(avgLoad)}</div>
                    </div>
                    <div>
                      <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">Peak</div>
                      <div className="mt-[6px] font-mono text-[22px] font-semibold leading-none text-ink">
                        {fmtLoad(peakVal)}<span className="ml-[3px] text-[12px] font-medium text-mute">· W{peakWk}</span>
                      </div>
                    </div>
                  </div>
                  <Chart
                    width={600}
                    height={130}
                    padding={{ top: 6, right: 2, bottom: 2, left: 2 }}
                    yDomain={[0, loadMax]}
                    ariaLabel={`Weekly training load, last ${weeks} weeks`}
                    className="h-[130px] w-full"
                  >
                    <Bars
                      data={loadSeries.map((v, i) => ({ key: `W${i + 1}`, value: v }))}
                      color="series"
                      baseColor={colors.categorical[1]}
                      emphasisKey={`W${nowIdx + 1}`}
                    />
                  </Chart>
                  <div className="mt-[10px] flex justify-between font-mono text-[9px] leading-none text-dim"><span>W1</span><span className="text-ac">W{weeks} · now</span></div>
                </>
              )}
            </ResourceState>
          </Card>
          <LoadBalanceCard acwr={acwr} acute={thisWeek} chronic={chronic} />
        </div>

        <FieldTestLogCard resource={obsRes} />
      </div>
    </section>
  );
}
