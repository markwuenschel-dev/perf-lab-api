// src/perflab/screens/TwinScreen.tsx
import type { ReactNode } from "react";
import { usePerfLab } from "../store";
import { Card, MetricBar, Pill, ReadinessRing, SectionLabel, SyncChip } from "../ui";
import {
  CAP_CFG,
  CAP_TIPS,
  COLORS,
  DAYS,
  DAY_COUNT,
  FATIGUE_ORDER,
  fatigueColor,
  readinessColor,
  readinessNote,
  readinessWord,
  SKILL_DEFS,
  swatch,
  swatchLite,
  TISSUE_ORDER,
} from "../sim";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtPt = ([x, y]: [number, number]) => `${x.toFixed(1)},${y.toFixed(1)}`;

export function TwinScreen() {
  const { state, actions } = usePerfLab();
  const N = DAY_COUNT;
  let di = state.twinDayIdx;
  if (di == null || di > N - 1) di = N - 1;
  if (di < 0) di = 0;
  const D = DAYS[di];
  const isToday = di === N - 1;
  const tDate = `${D.date.getDate()} ${MONTHS[D.date.getMonth()]}`;
  const daysAgo = N - 1 - di;
  const tWhen = daysAgo === 0 ? "Today" : daysAgo === 1 ? "Yesterday" : `${daysAgo} days ago`;
  const rc = readinessColor(D.readiness);

  // top sparkline
  const Wv = 560, Hv = 46;
  const xsf = (i: number) => 10 + (i / (N - 1)) * (Wv - 20);
  const ysf = (r: number) => Hv - 5 - ((r - 20) / 80) * (Hv - 12);
  const tSpark = DAYS.map((d, i) => `${xsf(i).toFixed(1)},${ysf(d.readiness).toFixed(1)}`).join(" ");

  // capacities
  const tCaps = CAP_CFG.map((c, idx) => {
    const v = D.C[c.key];
    const pct = Math.max(4, Math.min(100, (v / c.max) * 100));
    const sub = c.base != null ? `+${v - c.base} vs ${c.base}` : c.sub;
    return { label: c.label, val: v, sub, tip: CAP_TIPS[c.key], key: c.key, first: idx === 0, pct };
  });

  // radar
  const radCx = 92, radCy = 96, radR = 64;
  const ang = (k: number) => -Math.PI / 2 + (k * 2 * Math.PI) / 5;
  const pt = (k: number, r: number): [number, number] => [radCx + Math.cos(ang(k)) * r, radCy + Math.sin(ang(k)) * r];
  const radVals = CAP_CFG.map((c) => Math.max(0.06, Math.min(1, D.C[c.key] / c.max)));
  const radPoly = radVals.map((v, k) => fmtPt(pt(k, v * radR))).join(" ");
  const radGrid = [0.25, 0.5, 0.75, 1].map((g) => CAP_CFG.map((_, k) => fmtPt(pt(k, g * radR))).join(" "));
  const radSpokes = CAP_CFG.map((_, k) => pt(k, radR));
  const radShort = ["Aerobic", "Glyco", "Strength", "Power", "Work"];
  const radLabels = CAP_CFG.map((_, k) => {
    const [x, y] = pt(k, radR + 15);
    return { x, y: y + 3, label: radShort[k] };
  });
  const base0 = DAYS[0].C;
  const radBaseVals = CAP_CFG.map((c) => Math.max(0.04, Math.min(1, base0[c.key] / c.max)));
  const radBasePoly = radBaseVals.map((v, k) => fmtPt(pt(k, v * radR))).join(" ");
  const radDots = radVals.map((v, k) => pt(k, v * radR));
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

  // tissue body
  const reg = (k: string) => {
    const c = fatigueColor(D.T[k]);
    return { fill: swatch(c), stroke: c };
  };
  const tm = { knee: reg("Knee"), lumbar: reg("Lumbar"), hip: reg("Hip"), ankle: reg("Ankle"), shoulder: reg("Shoulder"), elbow: reg("Elbow"), wrist: reg("Wrist"), finger: reg("Finger") };
  const tHalo = swatchLite(fatigueColor(D.T.Knee));

  const sprog = 0.9 + 0.1 * (di / (N - 1));
  const tSignalTrend = D.signal >= DAYS[Math.max(0, di - 1)].signal ? "↗ rising" : "→ steady";

  const bars = state.capView === "bars";
  const segBtn = (active: boolean) =>
    `rounded-[6px] border-0 px-3 py-[6px] font-mono text-[11px] font-semibold leading-none ${active ? "bg-ink text-[#0a0c10]" : "bg-transparent text-mute"}`;

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeaderTwin syncLabel={isToday ? "Synced 2h ago" : "Historical view"} onLog={actions.openLog} />

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
          <svg viewBox="0 0 560 46" preserveAspectRatio="none" className="block h-[38px] w-full">
            <line x1="0" y1="29" x2="560" y2="29" stroke="rgba(255,255,255,.05)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
            <polyline points={tSpark} fill="none" stroke="rgba(198,241,53,.55)" strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
            <circle cx={xsf(di)} cy={ysf(D.readiness)} r="4" fill="var(--ac)" />
          </svg>
          <input type="range" min={0} max={N - 1} value={di} onChange={(e) => actions.setTwinDay(+e.target.value)} className="mt-[2px] w-full cursor-pointer" style={{ accentColor: "var(--ac)" }} />
          <div className="mt-[2px] flex justify-between font-mono text-[9px] leading-none text-dim"><span>3 weeks ago</span><span>today</span></div>
        </div>
        <div className="flex flex-none items-center gap-[7px]">
          <button onClick={actions.dayPrev} className="h-[34px] w-[34px] rounded-[9px] border border-white/10 bg-white/[0.03] text-[15px] leading-none text-soft">‹</button>
          <button onClick={actions.dayNext} className="h-[34px] w-[34px] rounded-[9px] border border-white/10 bg-white/[0.03] text-[15px] leading-none text-soft">›</button>
          <button onClick={actions.dayToday} className="rounded-[9px] bg-ink px-[13px] py-[9px] text-[12px] font-semibold leading-none text-[#0a0c10]">Today</button>
        </div>
      </Card>

      {/* readiness + 4 tiles */}
      <div className="grid grid-cols-1 gap-[14px] lg:grid-cols-[300px_1fr]">
        <Card className="flex items-center gap-[18px]">
          <ReadinessRing value={D.readiness} color={rc} onClick={() => actions.openExplain("readiness")} />
          <div>
            <SectionLabel className="text-faint">Readiness</SectionLabel>
            <div className="mt-2 text-[18px] font-bold leading-none" style={{ color: rc }}>{readinessWord(D.readiness)}</div>
            <div className="mt-[9px] text-[11.5px] font-medium leading-[1.5] text-[#7c818c]">{readinessNote(D.readiness)}</div>
            <div className="mt-[10px] font-mono text-[10px] leading-none text-dim">100 − 0.55·F̄ − 0.45·Tₘₐₓ</div>
          </div>
        </Card>
        <div className="grid grid-cols-2 gap-[14px] lg:grid-cols-4">
          <MiniTile tip="Estimated maximal oxygen uptake (ml·kg⁻¹·min⁻¹) — your aerobic ceiling, derived from the 1.5-mile split. Age/sex not yet wired into v0.3." label="VO₂max" value={D.vo2.toFixed(1)} sub="ml·kg⁻¹·min⁻¹" foot="field test" footColor="text-teal" />
          <MiniTile tip="Speed↔endurance bias. Negative leans endurance, positive leans speed. A style index, not a fitness score — don't read negative as bad." label="Profile" value={D.profile.toFixed(1)} sub="speed ↔ endurance" foot="endurance-biased" footColor="text-info" />
          <MiniTile label="Habit" value={<>{D.habit}<span className="text-[16px] text-faint">%</span></>} sub="adherence" bar={D.habit} />
          <MiniTile tip="Structural adaptation drive — how strongly recent load is stimulating tissue remodelling. Higher = actively building structure." label="Struct. signal" value={D.signal.toFixed(1)} sub="adaptation drive" foot={tSignalTrend} footColor="text-teal" />
        </div>
      </div>

      {/* capacities */}
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
          <div className="grid grid-cols-1 items-center gap-[26px] lg:grid-cols-[280px_1fr_250px]">
            <div>
              <svg viewBox="0 0 184 200" className="block h-auto w-full overflow-visible">
                {radGrid.map((p, i) => (<polygon key={i} points={p} fill="none" stroke="rgba(255,255,255,.07)" strokeWidth="1" />))}
                {radSpokes.map(([x, y], i) => (<line key={i} x1="92" y1="96" x2={x} y2={y} stroke="rgba(255,255,255,.07)" strokeWidth="1" />))}
                <polygon points={radBasePoly} fill="none" stroke="rgba(255,255,255,.32)" strokeWidth="1.5" strokeDasharray="3 3" />
                <polygon points={radPoly} fill="rgba(198,241,53,.16)" stroke="var(--ac)" strokeWidth="2" strokeLinejoin="round" />
                {radDots.map(([x, y], i) => (<circle key={i} cx={x} cy={y} r="3" fill="var(--ac)" stroke="#0a0c10" strokeWidth="1.5" />))}
                {radLabels.map((l, i) => (<text key={i} x={l.x} y={l.y} fill="#9aa0ab" fontFamily="Geist" fontSize="9" fontWeight="600" textAnchor="middle">{l.label}</text>))}
              </svg>
              <div className="mt-2 flex justify-center gap-[18px] text-[10px] font-medium leading-none text-[#7c818c]">
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
              <div className="border-t border-white/[0.06] pt-3 text-[11px] font-medium leading-[1.5] text-[#7c818c]">{profileNote}</div>
            </div>
          </div>
        )}
      </Card>

      {/* fatigue + tissue */}
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
          <div className="grid grid-cols-[150px_1fr] items-center gap-[18px]">
            <svg viewBox="0 0 130 300" className="block h-auto w-full">
              <g fill="#1b212b" stroke="rgba(255,255,255,.06)" strokeWidth="1">
                <circle cx="65" cy="24" r="14" /><rect x="47" y="42" width="36" height="70" rx="15" /><rect x="28" y="46" width="13" height="74" rx="6.5" /><rect x="89" y="46" width="13" height="74" rx="6.5" /><rect x="45" y="104" width="40" height="26" rx="13" /><rect x="49" y="126" width="14" height="96" rx="7" /><rect x="67" y="126" width="14" height="96" rx="7" />
              </g>
              <circle cx="56" cy="172" r="12" fill={tHalo} className="animate-pl-pulse" /><circle cx="74" cy="172" r="12" fill={tHalo} className="animate-pl-pulse" />
              <g strokeWidth="1.5">
                <circle cx="40" cy="54" r="5.5" fill={tm.shoulder.fill} stroke={tm.shoulder.stroke} /><circle cx="90" cy="54" r="5.5" fill={tm.shoulder.fill} stroke={tm.shoulder.stroke} />
                <circle cx="34" cy="86" r="5.5" fill={tm.elbow.fill} stroke={tm.elbow.stroke} /><circle cx="96" cy="86" r="5.5" fill={tm.elbow.fill} stroke={tm.elbow.stroke} />
                <circle cx="34" cy="114" r="5.5" fill={tm.wrist.fill} stroke={tm.wrist.stroke} /><circle cx="96" cy="114" r="5.5" fill={tm.wrist.fill} stroke={tm.wrist.stroke} />
                <circle cx="34" cy="127" r="4" fill={tm.finger.fill} stroke={tm.finger.stroke} /><circle cx="96" cy="127" r="4" fill={tm.finger.fill} stroke={tm.finger.stroke} />
                <circle cx="65" cy="98" r="6" fill={tm.lumbar.fill} stroke={tm.lumbar.stroke} />
                <circle cx="54" cy="116" r="5.5" fill={tm.hip.fill} stroke={tm.hip.stroke} /><circle cx="76" cy="116" r="5.5" fill={tm.hip.fill} stroke={tm.hip.stroke} />
                <circle cx="56" cy="172" r="6.5" fill={tm.knee.fill} stroke={tm.knee.stroke} /><circle cx="74" cy="172" r="6.5" fill={tm.knee.fill} stroke={tm.knee.stroke} />
                <circle cx="56" cy="214" r="5.5" fill={tm.ankle.fill} stroke={tm.ankle.stroke} /><circle cx="74" cy="214" r="5.5" fill={tm.ankle.fill} stroke={tm.ankle.stroke} />
              </g>
            </svg>
            <div className="flex flex-col gap-[9px]">
              {TISSUE_ORDER.map((k) => {
                const v = D.T[k];
                const c = fatigueColor(v);
                return (
                  <div key={k} onClick={() => actions.openExplain(`T:${k}`)} className="flex cursor-pointer items-center gap-[9px]">
                    <span className="h-[7px] w-[7px] flex-none rounded-[2px]" style={{ background: c }} />
                    <span className="flex-1 text-[12px] font-medium leading-none" style={{ color: v >= 45 ? COLORS.soft : COLORS.mute }}>{k}</span>
                    <span className="font-mono text-[12px] font-semibold leading-none" style={{ color: v >= 45 ? c : COLORS.soft }}>{v}</span>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="mt-4 flex gap-4 border-t border-white/[0.06] pt-[14px] text-[11px] font-medium leading-none text-[#7c818c]">
            <span><span className="mr-[6px] inline-block h-[8px] w-[8px] rounded-[2px] bg-good" />ready</span>
            <span><span className="mr-[6px] inline-block h-[8px] w-[8px] rounded-[2px] bg-warn" />monitor</span>
            <span><span className="mr-[6px] inline-block h-[8px] w-[8px] rounded-[2px] bg-hot" />load high</span>
          </div>
        </Card>
      </div>

      {/* skills */}
      <Card className="px-[22px] py-5">
        <div className="mb-4 flex items-center justify-between">
          <SectionLabel>Skill state</SectionLabel>
          <div data-tip="Skill ratings 0–1 (shown as %) — technical proficiencies that gate how efficiently capacity converts to performance. Built through practice, not load." className="font-mono text-[10px] leading-none text-dim">0–1 · proficiency</div>
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
    </section>
  );
}

function ScreenHeaderTwin({ syncLabel, onLog }: { syncLabel: string; onLog: () => void }) {
  return (
    <header className="flex items-start justify-between gap-5">
      <div>
        <div className="flex items-center gap-[10px]">
          <h1 className="m-0 text-[25px] font-bold leading-none tracking-[-0.02em] text-ink">Digital Twin</h1>
          <Pill>S(t) · v0.3</Pill>
        </div>
        <p className="m-0 mt-[9px] max-w-[440px] text-[13.5px] font-medium leading-[1.5] text-[#7c818c]">Evolving state vector — capacities, fatigue &amp; tissue load. Seeded from the latest field-test handoff.</p>
      </div>
      <div className="flex items-center gap-[9px]">
        <SyncChip label={syncLabel} />
        <button onClick={onLog} className="rounded-[9px] bg-ink px-[15px] py-[9px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Log workout</button>
      </div>
    </header>
  );
}

function MiniTile({ label, value, sub, foot, footColor, bar, tip }: { label: string; value: ReactNode; sub: string; foot?: string; footColor?: string; bar?: number; tip?: string }) {
  return (
    <Card className="flex flex-col justify-between p-[17px]">
      <div data-tip={tip} className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">{label}</div>
      <div className="my-3">
        <span className="font-mono text-[28px] font-semibold leading-none text-ink">{value}</span>
        <div className="mt-[5px] text-[10px] font-medium leading-none text-faint">{sub}</div>
      </div>
      {bar != null ? (
        <div className="h-[5px] overflow-hidden rounded-full bg-white/[0.08]"><div className="h-full rounded-full bg-ac" style={{ width: `${bar}%` }} /></div>
      ) : (
        <div className={`font-mono text-[10px] leading-none ${footColor}`}>{foot}</div>
      )}
    </Card>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[11px] font-medium leading-none text-[#7c818c]">{k}</span>
      <span className={`text-[12px] font-semibold leading-none text-[#e6e8ec] ${mono ? "font-mono" : ""}`}>{v}</span>
    </div>
  );
}
