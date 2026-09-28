// src/perflab/screens/twin/GuestTwinPreview.tsx
//
// The GUEST Digital Twin: a clearly-labelled preview of a sample athlete, driven
// entirely by the deterministic sim (sim.ts). This is the ONLY twin path that
// shows the VO₂ / Profile / Skill mock tiles or opens the simulated Explain
// drawer.
//
// It lived inside TwinScreen.tsx, where 255 lines of sample-data rendering sat
// in the same file as the live screen. Splitting it out means the live twin file
// no longer carries a fixture render tree at all, and the boundary between
// "sampled" and "live" is a file boundary rather than a comment banner.
//
// Nothing here is authoritative. Every number is sim-derived except the VO₂ /
// Profile tiles when viewing today, which read the athlete's cached field test.

import { usePerfLab } from "../../store";
import { Card, MetricBar, ReadinessRing, SectionLabel } from "../../ui";
import { Chart, Line, Marker, Radar, useVizTheme } from "../../viz";
import {
  CAP_CFG,
  CAP_TIPS,
  DAYS,
  DAY_COUNT,
  FATIGUE_ORDER,
  fatigueColor,
  SKILL_DEFS,
} from "../../sim";
import { readinessColor, readinessNote, readinessWord } from "../../readinessPresentation";
import { MiniTile } from "./MiniTile";
import { TissueBodyMap } from "./TissueBodyMap";
import { dayMonthLabel } from "./viewingLabel";

export function GuestTwinPreview() {
  const { state, actions } = usePerfLab();
  const { accent } = useVizTheme();
  const N = DAY_COUNT;
  let di = state.twinDayIdx;
  if (di == null || di > N - 1) di = N - 1;
  if (di < 0) di = 0;
  const D = DAYS[di];
  const isToday = di === N - 1;
  const tDate = dayMonthLabel(D.date);
  const daysAgo = N - 1 - di;
  const tWhen = daysAgo === 0 ? "Today" : daysAgo === 1 ? "Yesterday" : `${daysAgo} days ago`;
  const rc = readinessColor(D.readiness);

  const clampDay = (i: number) => Math.max(0, Math.min(N - 1, i));

  // VO₂ / Profile tiles read the cached field test when viewing today; the sim
  // backs the historical days (and the case where no field test has been run).
  const ft = isToday ? state.fieldTest : null;
  const tVo2 = ft ? ft.vo2_max : D.vo2;
  const tProfileVal = ft ? ft.fatigue_percent : D.profile;
  const tProfileFoot = ft ? ft.fatigue_profile : "endurance-biased";

  const sparkData = DAYS.map((d, i) => [i, d.readiness] as [number, number]);

  const tCaps = CAP_CFG.map((c, idx) => {
    const v = D.C[c.key];
    const pct = Math.max(4, Math.min(100, (v / c.max) * 100));
    const sub = c.base != null ? `+${v - c.base} vs ${c.base}` : c.sub;
    return { label: c.label, val: v, sub, tip: CAP_TIPS[c.key], key: c.key, first: idx === 0, pct };
  });

  const radVals = CAP_CFG.map((c) => Math.max(0.06, Math.min(1, D.C[c.key] / c.max)));
  const radShort = ["Aerobic", "Glyco", "Strength", "Power", "Work"];
  const base0 = DAYS[0].C;
  const radarAxes = CAP_CFG.map((c, k) => ({ key: c.key, label: radShort[k], value: D.C[c.key], max: c.max }));
  const radarBaseline = CAP_CFG.map((c) => base0[c.key]);
  const axisRows = CAP_CFG.map((c, k) => {
    const cur = D.C[c.key];
    const dl = cur - base0[c.key];
    return { label: c.label, val: cur, delta: `${dl >= 0 ? "+" : ""}${dl}`, tip: CAP_TIPS[c.key], pct: Math.round(radVals[k] * 100) };
  });
  const domIdx = radVals.indexOf(Math.max(...radVals));
  const domAxis = CAP_CFG[domIdx].label;
  const typeNames = ["Aerobic engine", "Glycolytic / speed", "Strength-led", "Power-led", "Durability-led"];
  const minN = Math.min(...radVals), maxN = Math.max(...radVals);
  const balPct = Math.round((minN / maxN) * 100);
  const balanceWord = balPct >= 80 ? "Well-rounded" : balPct >= 62 ? "Moderately specialised" : "Highly specialised";
  const composite = Math.round((radVals.reduce((a, b) => a + b, 0) / radVals.length) * 100);
  const profileNote = `Strongest in ${domAxis.toLowerCase()}. ${balPct >= 80 ? "Capacities are evenly developed across all axes." : "Development skews toward the leading axes — room to round out the lower ones."}`;

  const sprog = 0.9 + 0.1 * (di / (N - 1));
  const tSignalTrend = D.signal >= DAYS[Math.max(0, di - 1)].signal ? "↗ rising" : "→ steady";

  const bars = state.capView === "bars";
  const segBtn = (active: boolean) =>
    `rounded-[6px] border-0 px-3 py-[6px] font-mono text-[11px] font-semibold leading-none ${active ? "bg-ink text-[#0a0c10]" : "bg-transparent text-mute"}`;

  return (
    <>
      <div className="flex items-center gap-2 rounded-[12px] border border-mint/25 bg-mint/[0.08] px-4 py-[10px] text-[12px] font-medium leading-none text-[#9ad6c8]">
        <span className="h-[7px] w-[7px] flex-none rounded-full bg-ac" />
        Preview — sample athlete. Sign in to see your own live twin.
      </div>

      {/* time-travel */}
      <Card className="flex items-center gap-[22px] px-5 py-[15px]">
        <div className="min-w-[118px] flex-none">
          <SectionLabel className="text-faint">Viewing</SectionLabel>
          <div className="mt-[7px] flex items-baseline gap-2">
            <span className="text-[18px] font-bold leading-none text-ink">{tDate}</span>
            <span className="text-[11px] font-medium leading-none text-teal">{tWhen}</span>
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <Chart
            width={560}
            height={40}
            padding={{ top: 5, right: 10, bottom: 5, left: 10 }}
            xDomain={[0, N - 1]}
            yDomain={[20, 100]}
            ariaLabel="Sample readiness across the preview window"
            className="h-[38px] w-full"
          >
            <Line data={sparkData} color={accent} opacity={0.7} />
            <Marker x={di} y={D.readiness} color={accent} />
          </Chart>
          <input type="range" min={0} max={N - 1} value={di} onChange={(e) => actions.setTwinDay(+e.target.value)} className="mt-[2px] w-full cursor-pointer" style={{ accentColor: "var(--ac)" }} />
          <div className="mt-[2px] font-mono text-[9px] leading-none text-dim">sample history</div>
        </div>
        <div className="flex flex-none items-center gap-[7px]">
          <button onClick={() => actions.setTwinDay(clampDay(di - 1))} className="h-[34px] w-[34px] rounded-[9px] border border-white/[0.07] bg-white/[0.04] text-[15px] leading-none text-soft">‹</button>
          <button onClick={() => actions.setTwinDay(clampDay(di + 1))} className="h-[34px] w-[34px] rounded-[9px] border border-white/[0.07] bg-white/[0.04] text-[15px] leading-none text-soft">›</button>
          <button onClick={() => actions.setTwinDay(N - 1)} className="rounded-[9px] bg-ink px-[13px] py-[9px] text-[12px] font-semibold leading-none text-canvas">Today</button>
        </div>
      </Card>

      {/* readiness + 4 sim tiles */}
      <div className="grid grid-cols-1 gap-[14px] lg:grid-cols-[300px_1fr]">
        <Card className="flex items-center gap-[18px]">
          <ReadinessRing value={D.readiness} color={rc} onClick={() => actions.openExplain("readiness")} />
          <div>
            <SectionLabel className="text-faint">Readiness</SectionLabel>
            <div className="mt-2 text-[18px] font-bold leading-none" style={{ color: rc }}>{readinessWord(D.readiness)}</div>
            <div className="mt-[9px] text-[11.5px] font-medium leading-[1.5] text-mute">{readinessNote(D.readiness)}</div>
          </div>
        </Card>
        <div className="grid grid-cols-2 gap-[14px] lg:grid-cols-4">
          <MiniTile tip="Estimated maximal oxygen uptake (ml·kg⁻¹·min⁻¹) — sample data." label="VO₂max" value={tVo2.toFixed(1)} sub="ml·kg⁻¹·min⁻¹" foot="field test" footColor="text-teal" />
          <MiniTile tip="Speed↔endurance bias — sample data." label="Profile" value={tProfileVal.toFixed(1)} sub="speed ↔ endurance" foot={tProfileFoot} footColor="text-info" />
          <MiniTile label="Habit" value={<>{D.habit}<span className="text-[16px] text-faint">%</span></>} sub="adherence" bar={D.habit} />
          <MiniTile tip="Structural adaptation drive — sample data." label="Struct. signal" value={D.signal.toFixed(1)} sub="adaptation drive" foot={tSignalTrend} footColor="text-teal" />
        </div>
      </div>

      {/* capacities (sim) */}
      <Card className="px-[22px] py-5">
        <div className="mb-[18px] flex items-center justify-between">
          <SectionLabel>Capacities · X(t)</SectionLabel>
          <div className="flex gap-[3px] rounded-[8px] border border-white/[0.08] p-[2px]">
            <button onClick={() => actions.setCapView("bars")} className={segBtn(bars)}>Bars</button>
            <button onClick={() => actions.setCapView("radar")} className={segBtn(!bars)}>Radar</button>
          </div>
        </div>
        {bars ? (
          <div className="grid grid-cols-2 gap-6 md:grid-cols-5">
            {tCaps.map((c) => (
              <div key={c.key} onClick={() => actions.openExplain(`X:${c.key}`)} className={`cursor-pointer ${c.first ? "" : "border-l border-white/[0.05] pl-6"}`}>
                <div data-tip={c.tip} className="mb-2 text-[12px] font-medium leading-none text-mute">{c.label}</div>
                <div className="font-mono text-[30px] font-semibold leading-none text-ink">{c.val}</div>
                <div className="mb-[7px] mt-[11px] h-[6px] overflow-hidden rounded-full bg-white/[0.07]">
                  <div className="h-full rounded-full" style={{ width: `${c.pct}%`, background: "linear-gradient(90deg,var(--ac),#a7e36e)" }} />
                </div>
                <div className="font-mono text-[10px] leading-none text-dim">{c.sub}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 items-center gap-[26px] lg:grid-cols-[330px_1fr_250px]">
            <div>
              <Radar axes={radarAxes} baseline={radarBaseline} size={200} className="mx-auto block h-auto w-full max-w-[300px]" />
              <div className="mt-2 flex justify-center gap-[18px] text-[10px] font-medium leading-none text-mute">
                <span><span className="mr-[5px] inline-block h-[3px] w-[12px] rounded-[2px] bg-ac align-middle" />now</span>
                <span><span className="mr-[5px] inline-block w-[12px] border-t-[1.5px] border-dashed border-white/50 align-middle" />block start</span>
              </div>
            </div>
            <div className="flex flex-col gap-[13px]">
              <div className="flex items-center justify-between font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-dim"><span>Axis · normalized</span><span>vs start</span></div>
              {axisRows.map((a) => (
                <div key={a.label} className="flex items-center gap-3">
                  <span data-tip={a.tip} className="w-[96px] flex-none text-[12px] font-medium leading-none text-mute">{a.label}</span>
                  <span className="w-[40px] flex-none font-mono text-[16px] font-semibold leading-none text-ink">{a.val}</span>
                  <div className="h-[6px] flex-1 overflow-hidden rounded-full bg-white/[0.07]"><div className="h-full rounded-full" style={{ width: `${a.pct}%`, background: "linear-gradient(90deg,var(--ac),#a7e36e)" }} /></div>
                  <span className="w-[34px] text-right font-mono text-[11px] font-semibold leading-none text-teal">{a.delta}</span>
                </div>
              ))}
            </div>
            <div className="flex flex-col justify-center gap-[14px] self-stretch rounded-[14px] border border-white/[0.06] bg-white/[0.02] p-[18px]">
              <div>
                <SectionLabel className="text-faint">Profile shape</SectionLabel>
                <div className="mt-[9px] text-[19px] font-bold leading-[1.1] text-ac">{typeNames[domIdx]}</div>
              </div>
              <div className="flex flex-col gap-[9px]">
                <Row k="Dominant axis" v={domAxis} />
                <Row k="Composite" v={`${composite}`} mono />
                <Row k="Balance" v={balanceWord} />
              </div>
              <div className="border-t border-white/[0.06] pt-3 text-[11px] font-medium leading-[1.5] text-mute">{profileNote}</div>
            </div>
          </div>
        )}
      </Card>

      {/* fatigue + tissue (sim) */}
      <div className="grid grid-cols-1 gap-[14px] lg:grid-cols-2">
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <SectionLabel>Fatigue · F(t)</SectionLabel>
            <div className="font-mono text-[10px] leading-none text-dim">0 fresh → 100 maxed</div>
          </div>
          <div className="flex flex-col gap-[13px]">
            {FATIGUE_ORDER.map((k) => (
              <MetricBar key={k} label={k} value={D.F[k]} pct={D.F[k]} color={fatigueColor(D.F[k])} onClick={() => actions.openExplain(`F:${k}`)} labelClassName="w-[74px]" valueClassName="w-[26px] text-soft" />
            ))}
          </div>
        </Card>
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <SectionLabel>Tissue load · T(t)</SectionLabel>
            <div className="font-mono text-[10px] leading-none text-dim">local stress, not injury</div>
          </div>
          <TissueBodyMap getT={(k) => D.T[k]} onRegionClick={(k) => actions.openExplain(`T:${k}`)} />
        </Card>
      </div>

      {/* skills (sim) */}
      <Card className="px-[22px] py-5">
        <div className="mb-4 flex items-center justify-between">
          <SectionLabel>Skill state</SectionLabel>
          <div data-tip="Skill ratings 0–1 (shown as %) — sample data." className="font-mono text-[10px] leading-none text-dim">0–1 · proficiency</div>
        </div>
        <div className="grid grid-cols-1 gap-x-7 gap-y-[13px] md:grid-cols-2">
          {SKILL_DEFS.map((sd) => {
            const pc = Math.min(100, Math.round(sd.base * sprog * 100));
            return (
              <MetricBar key={sd.label} label={sd.label} value={`${pc}%`} pct={pc} color="linear-gradient(90deg,#45d6c4,#7bd6c0)" labelClassName="w-[118px]" valueClassName="w-[36px] text-[#9ad6c8]" />
            );
          })}
        </div>
      </Card>
    </>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[11px] font-medium leading-none text-mute">{k}</span>
      <span className={`text-[12px] font-semibold leading-none text-ink ${mono ? "font-mono" : ""}`}>{v}</span>
    </div>
  );
}
