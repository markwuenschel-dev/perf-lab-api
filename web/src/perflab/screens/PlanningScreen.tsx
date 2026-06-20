// src/perflab/screens/PlanningScreen.tsx
import { usePerfLab } from "../store";
import { Card, MetricBar, ScreenHeader, SectionLabel } from "../ui";
import { COLORS, PLAN_DAYS, PLAN_LOAD, PLAN_READY } from "../sim";

const WEEK: { day: string; title: string; sub: string; subColor: string; today?: boolean; rest?: boolean }[] = [
  { day: "Mon", title: "Recovery", sub: "Z1 · 8 km", subColor: "text-good" },
  { day: "Tue", title: "Endurance", sub: "Z2 · 14 km", subColor: "text-teal" },
  { day: "Wed", title: "Tempo intervals", sub: "Z3 · 5×6 min", subColor: "text-ac", today: true },
  { day: "Thu", title: "Recovery", sub: "Z1 · 6 km", subColor: "text-good" },
  { day: "Fri", title: "Threshold", sub: "Z4 · 4×8 min", subColor: "text-warn" },
  { day: "Sat", title: "Rest", sub: "", subColor: "", rest: true },
  { day: "Sun", title: "Long run", sub: "Z2 · 24 km", subColor: "text-teal" },
];

const DOSE: [string, number, number, string][] = [
  ["Volume", 5.5, 55, COLORS.teal],
  ["Intensity", 7.2, 72, "var(--ac)"],
  ["Density", 6.0, 60, "var(--ac)"],
  ["Impact", 4.0, 40, COLORS.warn],
  ["Skill", 2.2, 22, COLORS.good],
  ["Metabolic", 6.8, 68, "var(--ac)"],
];

export function PlanningScreen() {
  const { actions } = usePerfLab();

  const pcL = 24, pcR = 12, pcT = 14, pcB = 150, pcW = 680;
  const pcStep = (pcW - pcL - pcR) / 7;
  const pxc = (i: number) => pcL + pcStep * i + pcStep / 2;
  const ppy = (r: number) => pcT + (1 - (r - 30) / 70) * (pcB - pcT);
  const planBars = PLAN_DAYS.map((d, i) => {
    const cx = pxc(i);
    const bw = pcStep * 0.46;
    const h = (PLAN_LOAD[i] / 90) * (pcB - pcT);
    return { x: cx - bw / 2, y: pcB - h, w: bw, h, color: PLAN_LOAD[i] === 0 ? "#3a414b" : i === 2 ? COLORS.lime : COLORS.teal, day: d, labelX: cx };
  });
  const planLine = PLAN_DAYS.map((_, i) => `${pxc(i).toFixed(1)},${ppy(PLAN_READY[i]).toFixed(1)}`).join(" ");

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader title="Planning" subtitle="Adaptive prescription — each session is dosed against your current readiness and tissue load.">
        <div className="flex items-center gap-[7px] rounded-[9px] border border-ac/25 bg-ac/[0.1] px-[13px] py-[9px] font-mono text-[11px] font-semibold leading-none text-ac">
          <span className="h-[7px] w-[7px] rounded-full bg-ac" />Readiness 64 · holding intensity
        </div>
      </ScreenHeader>

      {/* week strip */}
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

      {/* load vs readiness */}
      <Card className="px-[22px] py-5">
        <div className="mb-2 flex items-center justify-between">
          <SectionLabel>Projected load vs readiness</SectionLabel>
          <div className="flex gap-4 text-[11px] font-medium leading-none text-[#7c818c]">
            <span><span className="mr-[6px] inline-block h-[9px] w-[9px] rounded-[2px] bg-teal" />session load</span>
            <span><span className="mr-[6px] inline-block h-[3px] w-[11px] rounded-[2px] bg-ac align-middle" />readiness</span>
          </div>
        </div>
        <svg viewBox="0 0 680 180" className="block h-[200px] w-full overflow-visible">
          <line x1="24" y1="150" x2="668" y2="150" stroke="rgba(255,255,255,.08)" strokeWidth="1" />
          <line x1="24" y1="96" x2="668" y2="96" stroke="rgba(255,255,255,.04)" strokeWidth="1" />
          {planBars.map((b, i) => (<rect key={i} x={b.x} y={b.y} width={b.w} height={b.h} rx="4" fill={b.color} />))}
          <polyline points={planLine} fill="none" stroke="var(--ac)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
          {PLAN_DAYS.map((_, i) => (<circle key={i} cx={pxc(i)} cy={ppy(PLAN_READY[i])} r="3.5" fill="#0a0c10" stroke="var(--ac)" strokeWidth="2" />))}
          {planBars.map((b, i) => (<text key={i} x={b.labelX} y="168" fill="#646b78" fontFamily="Geist Mono" fontSize="11" textAnchor="middle">{b.day}</text>))}
        </svg>
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
