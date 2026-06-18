// src/perflab/screens/HistoryScreen.tsx
import { usePerfLab } from "../store";
import { Card, SectionLabel, Track } from "../ui";
import { DAYS, DAY_COUNT } from "../sim";

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
  const N = DAY_COUNT;
  const hW = 600, hpadX = 10, hTop = 14, hBot = 165;
  const hx = (i: number) => hpadX + (i / (N - 1)) * (hW - 2 * hpadX);
  const hy = (r: number) => hBot - ((r - 20) / 80) * (hBot - hTop);
  const hReadyLine = DAYS.map((d, i) => `${hx(i).toFixed(1)},${hy(d.readiness).toFixed(1)}`).join(" ");
  let hReadyArea = `M ${hx(0).toFixed(1)} ${hBot}`;
  DAYS.forEach((d, i) => (hReadyArea += ` L ${hx(i).toFixed(1)} ${hy(d.readiness).toFixed(1)}`));
  hReadyArea += ` L ${hx(N - 1).toFixed(1)} ${hBot} Z`;
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
          <p className="m-0 mt-[9px] max-w-[460px] text-[13.5px] font-medium leading-[1.5] text-[#7c818c]">How your twin and field tests have moved over the current 7-week build block.</p>
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
          <svg viewBox="0 0 600 180" preserveAspectRatio="none" className="mt-1 block h-[170px] w-full overflow-visible">
            <defs><linearGradient id="rdg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="rgba(198,241,53,.28)" /><stop offset="1" stopColor="rgba(198,241,53,0)" /></linearGradient></defs>
            {[60, 105, 150].map((y) => (<line key={y} x1="0" y1={y} x2="600" y2={y} stroke="rgba(255,255,255,.05)" strokeWidth="1" vectorEffect="non-scaling-stroke" />))}
            <path d={hReadyArea} fill="url(#rdg)" />
            <polyline points={hReadyLine} fill="none" stroke="var(--ac)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
            {DAYS.map((d, i) => (<circle key={`d${i}`} cx={hx(i)} cy={hy(d.readiness)} r="2.5" fill="var(--ac)" style={{ pointerEvents: "none" }} />))}
            {DAYS.map((d, i) => (<circle key={`h${i}`} cx={hx(i)} cy={hy(d.readiness)} r="9" fill="rgba(255,255,255,0)" onClick={() => goDay(i)} className="cursor-pointer" />))}
          </svg>
        </Card>
        <div className="flex flex-col gap-4">
          <div className="rounded-[18px] border border-mint/[0.18] p-[18px]" style={{ background: "linear-gradient(120deg,#0f1f1c,#111419 60%)" }}>
            <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#9ad6c8]">VO₂max progression</div>
            <div className="mt-3 flex items-end gap-2"><span className="font-mono text-[28px] font-semibold leading-none text-ink">58.4</span><span className="mb-1 text-[11px] font-medium leading-none text-good">+4.3 since Apr</span></div>
            <div className="mt-3 font-mono text-[11px] leading-none text-[#7c818c]">54.1 → 55.2 → 56.9 → 58.4</div>
          </div>
          <Card className="p-[18px]">
            <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">Aerobic capacity</div>
            <div className="mt-3 flex items-end gap-2"><span className="font-mono text-[28px] font-semibold leading-none text-ink">320</span><span className="mb-1 text-[11px] font-medium leading-none text-good">+40 vs base</span></div>
            <Track pct={80} background="linear-gradient(90deg,var(--ac),#a7e36e)" className="mt-3 h-[6px]" />
          </Card>
        </div>
      </div>

      <Card className="px-[22px] py-5">
        <div className="mb-[18px] flex items-center justify-between">
          <SectionLabel>Weekly training load</SectionLabel>
          <div className="text-[11px] font-medium leading-none text-dim">arbitrary units · last 12 weeks</div>
        </div>
        <div className="flex h-[128px] items-end gap-[10px]">
          {LOAD_BARS.map(([h, active], i) => {
            const isNow = i === LOAD_BARS.length - 1;
            const bg = isNow ? "linear-gradient(180deg,var(--ac),#a7e36e)" : active ? "linear-gradient(180deg,#7bd6c0,#3a8a7c)" : "linear-gradient(180deg,#5a6470,#3a414b)";
            return (
              <div key={i} className="flex h-full flex-1 flex-col justify-end">
                <div className="rounded-[5px]" style={{ height: `${h}%`, background: bg, boxShadow: isNow ? "0 0 16px -4px rgba(198,241,53,.5)" : undefined }} />
              </div>
            );
          })}
        </div>
        <div className="mt-[10px] flex justify-between font-mono text-[9px] leading-none text-dim"><span>W1</span><span>W12 · now</span></div>
      </Card>

      <Card className="px-[22px] py-5">
        <SectionLabel className="mb-2">Field test log</SectionLabel>
        <div className="grid grid-cols-[1.2fr_1fr_1fr_1fr_1.4fr] gap-2 border-b border-white/[0.07] py-[10px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.1em] text-dim">
          <span>Date</span><span>300 m</span><span>1.5 mi</span><span>VO₂max</span><span>Profile</span>
        </div>
        {FT_LOG.map(([date, t3, t15, vo2, profile, latest], i) => (
          <div key={i} className="grid grid-cols-[1.2fr_1fr_1fr_1fr_1.4fr] items-center gap-2 border-b border-white/[0.05] py-[13px] last:border-0">
            <span className="text-[13px] font-semibold leading-none text-[#e6e8ec]">{date}{latest && <span className="ml-1 text-[10px] font-medium text-ac">latest</span>}</span>
            <span className="font-mono text-[13px] font-medium leading-none text-soft">{t3}</span>
            <span className="font-mono text-[13px] font-medium leading-none text-soft">{t15}</span>
            <span className={`font-mono text-[13px] font-semibold leading-none ${latest ? "text-teal" : "text-soft"}`}>{vo2}</span>
            <span className="text-[12px] font-medium leading-none text-info">{profile}</span>
          </div>
        ))}
      </Card>
    </section>
  );
}
