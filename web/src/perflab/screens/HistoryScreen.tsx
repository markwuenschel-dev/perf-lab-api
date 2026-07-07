// src/perflab/screens/HistoryScreen.tsx
import * as api from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import type { WellnessSampleOut } from "@/types";
import { usePerfLab } from "../store";
import { useAuthedResource } from "../useAuthedResource";
import { Card, SectionLabel, Track } from "../ui";
import { Chart, Area, Axis, Bars, TableView, useChart, useVizTheme } from "../viz";
import { DAYS, DAY_COUNT } from "../sim";

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

const LOAD_BARS: [number, boolean][] = [
  [62, true], [58, true], [70, true], [65, true], [72, true], [48, false], [88, true], [82, true], [60, true], [90, true], [76, true], [74, true],
];

const FT_LOG: [string, string, string, string, string, boolean][] = [
  ["12 Jun", "0:52", "9:18", "58.4", "−7.2 · endurance", true],
  ["21 May", "0:53", "9:31", "56.9", "−6.1 · endurance", false],
  ["30 Apr", "0:54", "9:48", "55.2", "−5.4 · endurance", false],
  ["02 Apr", "0:55", "10:02", "54.1", "−4.8 · endurance", false],
];

export function HistoryScreen() {
  const { actions } = usePerfLab();
  const { accent, colors } = useVizTheme();
  const N = DAY_COUNT;
  const readinessSeries = DAYS.map((d) => d.readiness);
  const hDiff = DAYS[N - 1].readiness - DAYS[0].readiness;
  const hDelta = `${hDiff >= 0 ? "+" : ""}${hDiff} vs 3w ago`;
  const goDay = (i: number) => {
    actions.setTwinDay(i);
    actions.setScreen("twin");
  };

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <header className="flex items-start justify-between gap-5">
        <div>
          <h1 className="m-0 text-[25px] font-bold leading-none tracking-[-0.02em] text-ink">History</h1>
          <p className="m-0 mt-[9px] max-w-[460px] text-[13.5px] font-medium leading-[1.5] text-mute">How your twin and field tests have moved over the current 7-week build block.</p>
        </div>
        <div className="flex gap-[7px] rounded-[9px] border border-white/[0.08] p-[3px]">
          {["4w", "12w", "All"].map((t) => (
            <span key={t} className={`cursor-pointer rounded-[7px] px-[11px] py-[7px] text-[11px] font-semibold leading-none ${t === "12w" ? "bg-ink text-[#0a0c10]" : "text-faint"}`}>{t}</span>
          ))}
        </div>
      </header>

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
          <div className="rounded-[18px] border border-mint/[0.18] p-[18px]" style={{ background: "linear-gradient(120deg,#0f1f1c,#111419 60%)" }}>
            <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#9ad6c8]">VO₂max progression</div>
            <div className="mt-3 flex items-end gap-2"><span className="font-mono text-[28px] font-semibold leading-none text-ink">58.4</span><span className="mb-1 text-[11px] font-medium leading-none text-good">+4.3 since Apr</span></div>
            <div className="mt-3 font-mono text-[11px] leading-none text-mute">54.1 → 55.2 → 56.9 → 58.4</div>
          </div>
          <Card className="p-[18px]">
            <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">Aerobic capacity</div>
            <div className="mt-3 flex items-end gap-2"><span className="font-mono text-[28px] font-semibold leading-none text-ink">320</span><span className="mb-1 text-[11px] font-medium leading-none text-good">+40 vs base</span></div>
            <Track pct={80} background="linear-gradient(90deg,var(--ac),#a7e36e)" className="mt-3 h-[6px]" />
          </Card>
        </div>
      </div>

      <RecentWellnessCard />

      <Card className="px-[22px] py-5">
        <div className="mb-[18px] flex items-center justify-between">
          <SectionLabel>Weekly training load</SectionLabel>
          <div className="text-[11px] font-medium leading-none text-dim">arbitrary units · last 12 weeks</div>
        </div>
        <Chart
          width={600}
          height={128}
          padding={{ top: 6, right: 2, bottom: 2, left: 2 }}
          yDomain={[0, 100]}
          ariaLabel="Weekly training load, last 12 weeks"
          className="h-[128px] w-full"
        >
          <Bars
            data={LOAD_BARS.map(([h], i) => ({ key: `W${i + 1}`, value: h }))}
            color="series"
            baseColor={colors.categorical[1]}
            emphasisKey={`W${LOAD_BARS.length}`}
          />
        </Chart>
        <div className="mt-[10px] flex justify-between font-mono text-[9px] leading-none text-dim"><span>W1</span><span>W12 · now</span></div>
      </Card>

      <Card className="px-[22px] py-5">
        <SectionLabel className="mb-2">Field test log</SectionLabel>
        <TableView
          columns={[
            { key: "date", label: "Date" },
            { key: "t3", label: "300 m", numeric: true },
            { key: "t15", label: "1.5 mi", numeric: true },
            { key: "vo2", label: "VO₂max", numeric: true },
            { key: "profile", label: "Profile", align: "right" },
          ]}
          rows={FT_LOG.map(([date, t3, t15, vo2, profile, latest]) => ({
            date: (
              <span className="font-semibold text-ink">
                {date}
                {latest && <span className="ml-1 text-[10px] font-medium text-ac">latest</span>}
              </span>
            ),
            t3,
            t15,
            vo2: <span className={latest ? "font-semibold text-teal" : "font-semibold"}>{vo2}</span>,
            profile: <span className="text-info">{profile}</span>,
          }))}
        />
      </Card>
    </section>
  );
}
