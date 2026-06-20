// src/perflab/screens/GoalRaceScreen.tsx
import { usePerfLab } from "../store";
import { Card, Pill, SectionLabel, Tile } from "../ui";
import { COLORS, fmtHMS } from "../sim";

export function GoalRaceScreen() {
  const { state, actions } = usePerfLab();
  const rce = state.race;
  const twinVo2 = 58.4;
  const raceTenK = (41.5 - (twinVo2 - 55) * 0.95) * 60;
  const predSec = raceTenK * Math.pow(rce.distKm / 10, 1.06);
  const goalPace = rce.goalSec / rce.distKm;
  const gapSec = predSec - rce.goalSec;
  const gAbs = Math.abs(Math.round(gapSec));
  const raceGapStr = `${gapSec <= 0 ? "−" : "+"}${Math.floor(gAbs / 60)}:${String(gAbs % 60).padStart(2, "0")}`;
  const raceGapColor = gapSec <= 0 ? COLORS.good : COLORS.warn;
  const raceGapWord = gapSec <= 0 ? "ahead of goal" : "to find";
  const goalPaceLabel = `${Math.floor(goalPace / 60)}:${String(Math.round(goalPace % 60)).padStart(2, "0")} /km`;
  const marks = [
    { l: "10 km", km: 10 },
    { l: "Half", km: rce.distKm / 2 },
    { l: "30 km", km: 30 },
    { l: "40 km", km: 40 },
    { l: "Finish", km: rce.distKm },
  ];
  const splitRows = marks.filter((m) => m.km <= rce.distKm + 0.01).map((m) => ({ label: m.l, cum: fmtHMS(goalPace * m.km) }));
  const raceWeeksOut = Math.round(rce.daysToGo / 7);
  const racePhase = rce.daysToGo <= 14 ? "Taper · sharpen" : rce.daysToGo <= 56 ? "Build · race-specific" : "Base · aerobic";
  const predRange = `${fmtHMS(predSec * 0.985)} – ${fmtHMS(predSec * 1.015)}`;

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <header className="flex items-start justify-between gap-5">
        <div>
          <div className="flex items-center gap-[10px]">
            <h1 className="m-0 text-[25px] font-bold leading-none tracking-[-0.02em] text-ink">Goal Race</h1>
            <Pill>A-race · target</Pill>
          </div>
          <p className="m-0 mt-[9px] text-[13.5px] font-medium leading-[1.5] text-[#7c818c]">Everything in your plan points here. The twin predicts where you'll finish today.</p>
        </div>
        <button onClick={() => actions.setScreen("simulate")} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Refine path in simulator →</button>
      </header>

      <Card className="flex items-center justify-between gap-6 p-[26px]" style={{ background: "radial-gradient(120% 130% at 100% 0%,#11321f,#111419 55%)" }}>
        <div>
          <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.16em] text-ac">{rce.dateLabel}</div>
          <div className="mt-3 text-[30px] font-bold leading-[1.05] tracking-[-0.02em] text-ink">{rce.name}</div>
          <div className="mt-[10px] flex gap-[18px] text-[13px] font-medium leading-none text-mute">
            <span>{rce.loc}</span><span className="text-[#3a4049]">·</span><span>{rce.distName} · {rce.distKm.toFixed(1)} km</span>
          </div>
        </div>
        <div className="flex-none text-right">
          <div className="font-mono text-[64px] font-semibold leading-[0.9] tracking-[-0.03em] text-ink">{rce.daysToGo}</div>
          <div className="mt-[6px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.16em] text-faint">days to go</div>
          <div className="mt-3 inline-block rounded-[8px] border border-ac/[0.22] bg-ac/[0.1] px-[11px] py-[7px] text-[11px] font-semibold leading-none text-ac">{racePhase}</div>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Tile className="p-[18px]">
          <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">Goal time</div>
          <div className="mt-[11px] font-mono text-[28px] font-semibold leading-none text-ink">{fmtHMS(rce.goalSec)}</div>
          <div className="mt-2 text-[11px] font-medium leading-none text-faint">{goalPaceLabel}</div>
        </Tile>
        <Tile className="p-[18px]">
          <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">Predicted today</div>
          <div className="mt-[11px] font-mono text-[28px] font-semibold leading-none text-teal">{fmtHMS(predSec)}</div>
          <div className="mt-2 text-[11px] font-medium leading-none text-faint">range {predRange}</div>
        </Tile>
        <Tile className="p-[18px]">
          <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">Gap to goal</div>
          <div className="mt-[11px] font-mono text-[28px] font-semibold leading-none" style={{ color: raceGapColor }}>{raceGapStr}</div>
          <div className="mt-2 text-[11px] font-medium leading-none" style={{ color: raceGapColor }}>{raceGapWord}</div>
        </Tile>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
        <Card>
          <SectionLabel className="mb-[14px]">Goal pacing plan · cumulative</SectionLabel>
          <div className="flex flex-col gap-[2px]">
            {splitRows.map((sp) => (
              <div key={sp.label} className="flex items-center justify-between border-b border-white/[0.05] py-3 last:border-0">
                <span className="text-[13px] font-semibold leading-none text-[#e6e8ec]">{sp.label}</span>
                <span className="font-mono text-[14px] font-semibold leading-none text-soft">{sp.cum}</span>
              </div>
            ))}
          </div>
        </Card>
        <div className="flex flex-col gap-4">
          <Card>
            <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">Build status</div>
            <div className="mt-3 text-[17px] font-bold leading-none text-ink">{raceWeeksOut} weeks out</div>
            <div className="mt-[7px] text-[12px] font-medium leading-[1.4] text-[#7c818c]">{racePhase} — volume rising toward race-specific work before the taper.</div>
          </Card>
          <Card className="flex flex-col gap-[10px]">
            <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">Close the gap</div>
            <div className="text-[12.5px] font-medium leading-[1.5] text-mute">Test plans against this goal — see which volume and intensity reach {fmtHMS(rce.goalSec)} in time.</div>
            <button onClick={() => actions.setScreen("simulate")} className="mt-1 rounded-[9px] bg-white/[0.92] px-[14px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Open simulator →</button>
          </Card>
        </div>
      </div>
    </section>
  );
}
