// src/perflab/screens/HistoryScreen.tsx
import * as api from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import type { BenchmarkObservationRead, UnifiedStateVector, WellnessSampleOut, WorkoutLogSummary } from "@/types";
import { usePerfLab } from "../store";
import { useAuthedResource } from "../useAuthedResource";
import { Card, ScreenHeader, SectionLabel, Track } from "../ui";
import { Chart, Area, Axis, Bars, TableView, useChart, useVizTheme } from "../viz";
import { DAYS } from "../sim";

/** Readiness proxy ~ (1 − mean fatigue), scaled 0–100 — mirrors the backend
 *  `overall_readiness` intent for the trend line (same as Overview). */
function stateReadinessProxy(sv: UnifiedStateVector): number {
  const f = sv.fatigue_f;
  const vals = f
    ? [f.cns, f.muscular, f.metabolic, f.structural, f.tendon, f.grip]
    : [sv.f_nm_central, sv.f_nm_peripheral, sv.f_met_systemic, sv.f_struct_damage];
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
  return Math.round(Math.max(0, Math.min(100, 100 - mean)));
}

/** Aggregate logged workouts into the last `weeks` Mon-anchored weekly load totals
 *  (oldest→newest). Returns null when there's nothing to show. */
function weeklyLoad(workouts: WorkoutLogSummary[] | null, weeks = 12): number[] | null {
  if (!workouts || workouts.length === 0) return null;
  const msWeek = 7 * 864e5;
  const now = Date.now();
  const buckets = new Array(weeks).fill(0);
  let any = false;
  for (const w of workouts) {
    const t = new Date(w.session_timestamp ?? w.logged_at).getTime();
    if (Number.isNaN(t)) continue;
    const idx = weeks - 1 - Math.floor((now - t) / msWeek);
    if (idx >= 0 && idx < weeks) { buckets[idx] += w.total_volume_load ?? 0; any = true; }
  }
  return any ? buckets : null;
}

/** Compact load formatter — real volume-load totals run large, so thousands
 *  collapse to "12.4k" while the mock's small numbers stay whole. */
function fmtLoad(v: number): string {
  if (!Number.isFinite(v)) return "—";
  return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${Math.round(v)}`;
}

/** Acute:chronic workload ratio band. Optimal 0.8–1.3 is the injury-risk sweet
 *  spot from the training-load literature; below detrains, above ramps risk. */
function acwrBand(r: number): { label: string; color: string } {
  if (r < 0.8) return { label: "Detraining", color: "var(--color-info)" };
  if (r <= 1.3) return { label: "Optimal", color: "var(--color-good)" };
  if (r <= 1.5) return { label: "Ramping", color: "#e0a33a" };
  return { label: "Spike risk", color: "var(--color-hot)" };
}

/** Map a ratio onto the gauge's 0.5–2.0 visual scale (band edges 0.8/1.3/1.5
 *  land at 20% / 53.3% / 66.7%). */
const acwrPos = (r: number): number => Math.max(2, Math.min(98, ((r - 0.5) / 1.5) * 100));

// Load balance (ACWR) — reads the same weekly buckets the chart draws, so the
// gauge literally reflects the bars beside it: acute = this week, chronic = the
// trailing 4-week (28d) average. The band says whether that ramp is safe.
function LoadBalanceCard({ acwr, acute, chronic }: { acwr: number | null; acute: number; chronic: number }) {
  const band = acwr != null ? acwrBand(acwr) : null;
  return (
    <Card className="px-[22px] py-5">
      <div className="mb-3 flex items-center justify-between">
        <SectionLabel>Load balance</SectionLabel>
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
              style={{ background: "linear-gradient(90deg,var(--color-dim) 0 20%,var(--color-good) 20% 53.3%,#e0a33a 53.3% 66.7%,var(--color-hot) 66.7% 100%)" }}
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
            The 7-day vs 28-day workload ratio — is this week's load safe and progressing. The green band builds fitness without an injury-linked spike.
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
const cell = (v: number | null, suffix = ""): string => (v == null ? "—" : `${v}${suffix}`);

// Recent wellness — real daily samples from GET /v1/wellness. Renders live rows
// when the athlete has logged check-ins; guests and empty histories get a note
// rather than the prototype's mock series.
function RecentWellnessCard() {
  const { token } = useAuth();
  const { data, loading, error } = useAuthedResource<WellnessSampleOut[]>((t) => api.listWellness(t, 10), []);

  const body = !token ? (
    <div className="text-[13px] font-medium leading-[1.5] text-mute">Sign in and log a check-in to track your wellness here.</div>
  ) : loading ? (
    <div className="text-[13px] font-medium text-mute">Loading recent samples…</div>
  ) : error || !data || data.length === 0 ? (
    <div className="text-[13px] font-medium leading-[1.5] text-mute">No wellness logged yet — use Check-in to record sleep, HRV and resting HR.</div>
  ) : (
    <>
      <div className="grid grid-cols-[1.1fr_1fr_1fr_1fr_1fr] gap-2 border-b border-white/[0.07] py-[10px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.1em] text-dim">
        <span>Date</span><span>HRV</span><span>Sleep</span><span>RHR</span><span>Mood</span>
      </div>
      {data.map((w) => (
        <div key={w.id} className="grid grid-cols-[1.1fr_1fr_1fr_1fr_1fr] items-center gap-2 border-b border-white/[0.05] py-[12px] last:border-0">
          <span className="text-[13px] font-semibold leading-none text-ink">{fmtDay(w.date)}</span>
          <span className="font-mono text-[13px] font-medium leading-none text-soft">{cell(w.hrv_ms, " ms")}</span>
          <span className="font-mono text-[13px] font-medium leading-none text-soft">{cell(w.sleep_hours, " h")}</span>
          <span className="font-mono text-[13px] font-medium leading-none text-soft">{cell(w.resting_hr)}</span>
          <span className="font-mono text-[13px] font-medium leading-none text-soft">{cell(w.mood)}</span>
        </div>
      ))}
    </>
  );

  return (
    <Card className="px-[22px] py-5">
      <SectionLabel className="mb-2">Recent wellness</SectionLabel>
      {body}
    </Card>
  );
}

function FieldTestLogCard() {
  const { token } = useAuth();
  const { data, loading, error } = useAuthedResource<BenchmarkObservationRead[]>(
    (t) => api.listBenchmarkObservations(t, { benchmarkCode: FIELD_TEST_CODE, limit: 10 }),
    [],
  );

  const body = !token ? (
    <div className="text-[13px] font-medium leading-[1.5] text-mute">
      Sign in and log a field test to see your results here.
    </div>
  ) : loading ? (
    <div className="text-[13px] font-medium text-mute">Loading field tests…</div>
  ) : error ? (
    <div className="text-[13px] font-medium leading-[1.5] text-mute">
      Couldn&apos;t load your field tests — try again.
    </div>
  ) : !data || data.length === 0 ? (
    <div className="text-[13px] font-medium leading-[1.5] text-mute">
      No field tests logged yet — record one in Assess.
    </div>
  ) : (
    <TableView
      columns={[
        { key: "date", label: "Date" },
        { key: "vo2", label: "VO₂max", numeric: true },
        { key: "aerobicScore", label: "Aerobic score", numeric: true },
        { key: "validity", label: "Validity" },
      ]}
      rows={data.map((observation, index) => ({
        date: (
          <span className="font-semibold text-ink">
            {fmtDay(observation.observed_at)}
            {index === 0 && <span className="ml-1 text-[10px] font-medium text-ac">latest</span>}
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
        validity: observation.validity_status,
      }))}
    />
  );

  return (
    <Card className="px-[22px] py-5">
      <SectionLabel className="mb-2">Field test log</SectionLabel>
      {body}
    </Card>
  );
}

const LOAD_BARS: [number, boolean][] = [
  [62, true], [58, true], [70, true], [65, true], [72, true], [48, false], [88, true], [82, true], [60, true], [90, true], [76, true], [74, true],
];

export function HistoryScreen() {
  const { actions } = usePerfLab();
  const { token } = useAuth();
  const { accent, colors } = useVizTheme();

  // Real trends when signed in; the deterministic sim backs guests / empty history.
  const historyRes = useAuthedResource<UnifiedStateVector[]>((t) => api.getStateHistory(t, 22), []);
  const workoutsRes = useAuthedResource<WorkoutLogSummary[]>((t) => api.listWorkouts(t, 300), []);

  const realReadiness = token && historyRes.data && historyRes.data.length ? historyRes.data.map(stateReadinessProxy) : null;
  const readinessSeries = realReadiness ?? DAYS.map((d) => d.readiness);
  const N = readinessSeries.length;
  const hDiff = readinessSeries[N - 1] - readinessSeries[0];
  const hDelta = `${hDiff >= 0 ? "+" : ""}${hDiff} vs 3w ago`;

  const realLoad = token ? weeklyLoad(workoutsRes.data) : null;
  const loadSeries = realLoad ?? LOAD_BARS.map(([h]) => h);
  const loadMax = Math.max(1, ...loadSeries) * 1.1;
  const nowIdx = loadSeries.length - 1;

  // Weekly-load detail strip: this week vs last, block average, and the peak week.
  const thisWeek = loadSeries[nowIdx] ?? 0;
  const lastWeek = loadSeries.length >= 2 ? loadSeries[nowIdx - 1] : null;
  const avgLoad = loadSeries.length ? loadSeries.reduce((a, b) => a + b, 0) / loadSeries.length : 0;
  const peakVal = Math.max(0, ...loadSeries);
  const peakWk = loadSeries.indexOf(peakVal) + 1;
  const wowDelta = lastWeek && lastWeek > 0 ? (thisWeek - lastWeek) / lastWeek : null;

  // Acute:chronic balance from the same buckets — acute = current week, chronic =
  // trailing 4-week (28d) mean. Needs ≥2 weeks of real load or it's not meaningful.
  const recent4 = loadSeries.slice(-4);
  const chronic = recent4.length ? recent4.reduce((a, b) => a + b, 0) / recent4.length : 0;
  const acwr = chronic > 0 && recent4.filter((v) => v > 0).length >= 2 ? thisWeek / chronic : null;

  const goDay = (i: number) => {
    actions.setTwinDay(i);
    actions.setScreen("twin");
  };

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader title="History" subtitle="How your twin and assessments have moved over the current 7-week build block.">
        <div className="flex gap-[7px] rounded-[9px] border border-white/[0.08] p-[3px]">
          {["4w", "12w", "All"].map((t) => (
            <span key={t} className={`cursor-pointer rounded-[7px] px-[11px] py-[7px] text-[11px] font-semibold leading-none ${t === "12w" ? "bg-ink text-[#0a0c10]" : "text-faint"}`}>{t}</span>
          ))}
        </div>
      </ScreenHeader>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_300px]">
        <Card className="px-[22px] py-5">
          <div className="mb-2 flex items-start justify-between">
            <div>
              <SectionLabel>Readiness</SectionLabel>
              <div className="mt-2 flex items-end gap-2">
                <span className="font-mono text-[30px] font-semibold leading-none text-ink">64</span>
                <span className="mb-1 text-[11px] font-medium leading-none text-good">{hDelta}</span>
              </div>
            </div>
            <div className="text-right font-mono text-[10px] leading-none text-dim">3-week · click to time-travel</div>
          </div>
          <Chart
            width={600}
            height={180}
            padding={{ top: 14, right: 10, bottom: 15, left: 10 }}
            xDomain={[0, N - 1]}
            yDomain={[20, 100]}
            ariaLabel="Readiness across the current build block"
            className="mt-1 h-[170px] w-full"
          >
            <Axis y yTicks={3} />
            <Area data={readinessSeries.map((r, i) => [i, r] as [number, number])} color={accent} />
            <DayMarkers readiness={readinessSeries} onPick={goDay} color={accent} />
          </Chart>
        </Card>
        <div className="flex flex-col gap-4">
          <Card className="border-mint/[0.18] p-[18px]" style={{ background: "linear-gradient(120deg,#0f1f1c,#111419 60%)" }}>
            <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#9ad6c8]">VO₂max progression</div>
            <div className="mt-3 flex items-end gap-2"><span className="font-mono text-[28px] font-semibold leading-none text-ink">58.4</span><span className="mb-1 text-[11px] font-medium leading-none text-good">+4.3 since Apr</span></div>
            <div className="mt-3 font-mono text-[11px] leading-none text-mute">54.1 → 55.2 → 56.9 → 58.4</div>
          </Card>
          <Card className="p-[18px]">
            <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">Aerobic capacity</div>
            <div className="mt-3 flex items-end gap-2"><span className="font-mono text-[28px] font-semibold leading-none text-ink">320</span><span className="mb-1 text-[11px] font-medium leading-none text-good">+40 vs base</span></div>
            <Track pct={80} background="linear-gradient(90deg,var(--ac),#a7e36e)" className="mt-3 h-[6px]" />
          </Card>
        </div>
      </div>

      <RecentWellnessCard />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
        <Card className="px-[22px] py-5">
          <div className="mb-4 flex items-center justify-between">
            <SectionLabel>Weekly training load</SectionLabel>
            <div className="text-[11px] font-medium leading-none text-dim">volume load · last 12 weeks</div>
          </div>
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
              <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">12-wk avg</div>
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
            height={100}
            padding={{ top: 6, right: 2, bottom: 2, left: 2 }}
            yDomain={[0, loadMax]}
            ariaLabel="Weekly training load, last 12 weeks"
            className="h-[100px] w-full"
          >
            <Bars
              data={loadSeries.map((v, i) => ({ key: `W${i + 1}`, value: v }))}
              color="series"
              baseColor={colors.categorical[1]}
              emphasisKey={`W${nowIdx + 1}`}
            />
          </Chart>
          <div className="mt-[10px] flex justify-between font-mono text-[9px] leading-none text-dim"><span>W1</span><span>W12 · now</span></div>
        </Card>
        <LoadBalanceCard acwr={acwr} acute={thisWeek} chronic={chronic} />
      </div>

      <FieldTestLogCard />
    </section>
  );
}
