// src/perflab/screens/SimulatorScreen.tsx
import { cn } from "@/lib/utils";
import { usePerfLab } from "../store";
import { Card, Pill, ScreenHeader, SectionLabel, Tile } from "../ui";
import { buildProjection, COLORS, fmtMin } from "../sim";

const seg = (active: boolean) =>
  cn(
    "flex-1 cursor-pointer rounded-[9px] border px-[6px] py-[10px] text-center text-[12px] font-semibold leading-none transition-colors",
    active ? "border-ac/40 bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute",
  );

export function SimulatorScreen() {
  const { state, actions } = usePerfLab();
  const sim = state.sim;
  const proj = buildProjection(sim);
  const base = buildProjection({ volume: 48, intensity: "balanced", weeks: sim.weeks, recovery: "standard" });

  const vo2Final = proj.vo2Final;
  const vo2Delta = vo2Final - base.vo2Final;
  const vo2DeltaStr = `${vo2Delta >= 0 ? "+" : ""}${vo2Delta.toFixed(1)}`;
  const tenKSecSaved = Math.round((base.tenKMin - proj.tenKMin) * 60);
  const tenKDeltaStr = `${tenKSecSaved >= 0 ? "−" : "+"}${Math.floor(Math.abs(tenKSecSaved) / 60)}:${String(Math.abs(tenKSecSaved) % 60).padStart(2, "0")}`;
  const tenKColor = tenKSecSaved >= 0 ? COLORS.good : COLORS.warn;
  const riskBand = proj.risk < 25 ? "Low" : proj.risk < 50 ? "Moderate" : "High";
  const riskColor = proj.risk < 25 ? COLORS.good : proj.risk < 50 ? COLORS.warn : COLORS.hot;
  const peakColor = proj.peakFat < 45 ? COLORS.good : proj.peakFat < 65 ? COLORS.warn : COLORS.hot;

  const cW = 520, cpL = 6, cpR = 6, cpT = 14, cH = 188, cpB = 26;
  const allV = proj.vo2.concat(base.vo2);
  const vMax = Math.ceil(Math.max(...allV)) + 1;
  const vMin = Math.floor(Math.min(...allV)) - 1;
  const cN = sim.weeks;
  const cx = (i: number) => cpL + (i / cN) * (cW - cpL - cpR);
  const cy = (v: number) => cpT + (1 - (v - vMin) / (vMax - vMin)) * (cH - cpT - cpB);
  const simPlanPts = proj.vo2.map((v, i) => `${cx(i).toFixed(1)},${cy(v).toFixed(1)}`).join(" ");
  const simBasePts = base.vo2.map((v, i) => `${cx(i).toFixed(1)},${cy(v).toFixed(1)}`).join(" ");
  let simArea = `M ${cx(0).toFixed(1)} ${cy(proj.vo2[0]).toFixed(1)}`;
  proj.vo2.forEach((v, i) => (simArea += ` L ${cx(i).toFixed(1)} ${cy(v).toFixed(1)}`));
  simArea += ` L ${cx(cN).toFixed(1)} ${cH - cpB} L ${cx(0).toFixed(1)} ${cH - cpB} Z`;

  const intWord = sim.intensity === "easy" ? "mostly easy" : sim.intensity === "hard" ? "high" : "balanced";
  const advice = proj.risk >= 50 ? "Add a recovery week or trim volume to stay clear of injury." : proj.risk >= 25 ? "Manageable alongside the scheduled down weeks." : "Comfortably within a safe ramp.";
  const simNarr = `Holding ~${sim.volume} km/wk at ${intWord} intensity for ${sim.weeks} weeks lifts projected VO₂max to ${vo2Final.toFixed(1)} (${vo2DeltaStr} vs maintaining), trimming your 10K to ${fmtMin(proj.tenKMin)}. Peak fatigue reaches ${proj.peakFat} and injury risk reads ${riskBand.toLowerCase()}. ${advice}`;

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader
        title="Twin Simulator"
        badge={<Pill>what-if · S(t) projection</Pill>}
        subtitle="Run your digital twin forward. Shape the plan on the left and watch projected VO₂, fatigue and injury risk respond — measured against simply maintaining."
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[360px_1fr]">
        <Card className="flex flex-col gap-5 self-start p-[22px]">
          <div>
            <SectionLabel className="mb-[11px]">Quick scenarios</SectionLabel>
            <div className="flex gap-2">
              {(["maintain", "build", "aggressive"] as const).map((p) => (
                <div key={p} onClick={() => actions.simPreset(p)} className="flex-1 cursor-pointer rounded-[10px] border border-white/10 bg-white/[0.03] px-[6px] py-[11px] text-center text-[12px] font-semibold capitalize leading-none text-soft">{p}</div>
              ))}
            </div>
          </div>
          <div className="h-px bg-white/[0.06]" />
          <div>
            <div className="mb-3 flex items-center justify-between">
              <SectionLabel>Weekly volume</SectionLabel>
              <span className="font-mono text-[13px] font-semibold leading-none text-ac">{sim.volume} km/wk</span>
            </div>
            <input type="range" min={30} max={90} step={2} value={sim.volume} onChange={(e) => actions.setSim({ volume: +e.target.value })} className="w-full cursor-pointer" style={{ accentColor: "var(--ac)" }} />
            <div className="mt-[6px] flex justify-between font-mono text-[10px] leading-none text-dim"><span>30</span><span>90 km</span></div>
          </div>
          <div>
            <SectionLabel className="mb-[11px]">Training intensity</SectionLabel>
            <div className="flex gap-2">
              {(["easy", "balanced", "hard"] as const).map((v) => (
                <div key={v} onClick={() => actions.setSim({ intensity: v })} className={`${seg(sim.intensity === v)} capitalize`}>{v}</div>
              ))}
            </div>
          </div>
          <div>
            <SectionLabel className="mb-[11px]">Recovery emphasis</SectionLabel>
            <div className="flex gap-2">
              {(["high", "standard", "minimal"] as const).map((v) => (
                <div key={v} onClick={() => actions.setSim({ recovery: v })} className={`${seg(sim.recovery === v)} capitalize`}>{v}</div>
              ))}
            </div>
          </div>
          <div>
            <SectionLabel className="mb-[11px]">Horizon</SectionLabel>
            <div className="flex gap-2">
              {[4, 8, 12, 16].map((w) => (
                <div key={w} onClick={() => actions.setSim({ weeks: w })} className={seg(sim.weeks === w)}>{w} wk</div>
              ))}
            </div>
          </div>
        </Card>

        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-[14px] lg:grid-cols-4">
            <Tile className="p-4">
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">VO₂max</div>
              <div className="mt-[11px] font-mono text-[26px] font-semibold leading-none text-teal">{vo2Final.toFixed(1)}</div>
              <div className="mt-2 text-[11px] font-semibold leading-none" style={{ color: vo2Delta >= 0 ? COLORS.good : COLORS.warn }}>{vo2DeltaStr} vs maintain</div>
            </Tile>
            <Tile className="p-4">
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">10K projection</div>
              <div className="mt-[11px] font-mono text-[26px] font-semibold leading-none text-ink">{fmtMin(proj.tenKMin)}</div>
              <div className="mt-2 text-[11px] font-semibold leading-none" style={{ color: tenKColor }}>{tenKDeltaStr} vs maintain</div>
            </Tile>
            <Tile className="p-4">
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">Peak fatigue</div>
              <div className="mt-[11px] font-mono text-[26px] font-semibold leading-none" style={{ color: peakColor }}>{proj.peakFat}</div>
              <div className="mt-2 text-[11px] font-medium leading-none text-faint">over horizon</div>
            </Tile>
            <Tile className="p-4">
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">Injury risk</div>
              <div className="mt-[11px] flex items-baseline gap-[7px]"><span className="font-mono text-[26px] font-semibold leading-none" style={{ color: riskColor }}>{proj.risk}</span><span className="text-[12px] font-semibold leading-none" style={{ color: riskColor }}>{riskBand}</span></div>
              <div className="mt-3 h-[5px] overflow-hidden rounded-full bg-white/[0.08]"><div className="h-full rounded-full transition-all" style={{ width: `${proj.risk}%`, background: riskColor }} /></div>
            </Tile>
          </div>

          <Card className="p-5">
            <div className="mb-[10px] flex items-center justify-between">
              <SectionLabel>Projected VO₂max trajectory</SectionLabel>
              <div className="flex items-center gap-4">
                <span className="flex items-center gap-[7px] text-[11px] font-medium leading-none text-soft"><span className="h-[3px] w-4 rounded-[2px] bg-ac" />This plan</span>
                <span className="flex items-center gap-[7px] text-[11px] font-medium leading-none text-mute"><span className="w-4 border-t-2 border-dashed border-[#5a626e]" />Maintain</span>
              </div>
            </div>
            <div className="mb-2 font-mono text-[10px] leading-none text-dim">VO₂ scale {vMin}–{vMax} ml·kg⁻¹·min⁻¹</div>
            <svg viewBox="0 0 520 188" preserveAspectRatio="none" className="block h-[230px] w-full overflow-visible">
              <defs><linearGradient id="simg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="rgba(198,241,53,.24)" /><stop offset="1" stopColor="rgba(198,241,53,0)" /></linearGradient></defs>
              <path d={simArea} fill="url(#simg)" />
              <polyline points={simBasePts} fill="none" stroke="#5a626e" strokeWidth="2" strokeDasharray="5 5" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
              <polyline points={simPlanPts} fill="none" stroke="var(--ac)" strokeWidth="2.5" vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
              <circle cx={cx(cN)} cy={cy(vo2Final)} r="4" fill="var(--ac)" />
            </svg>
            <div className="mt-1 flex justify-between font-mono text-[10px] leading-none text-dim"><span>now</span><span>{sim.weeks} wk</span></div>
          </Card>

          <Card className="flex items-start gap-[13px] px-5 py-[18px]">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="2" className="mt-[2px] flex-none"><path d="M12 2v6M12 22v-2M5 12H2M22 12h-3" /><circle cx="12" cy="12" r="4" /></svg>
            <div className="text-[13.5px] font-medium leading-[1.6] text-soft">{simNarr}</div>
          </Card>
        </div>
      </div>
    </section>
  );
}
