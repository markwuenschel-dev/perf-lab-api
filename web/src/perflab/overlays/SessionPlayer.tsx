// src/perflab/overlays/SessionPlayer.tsx
import { usePerfLab } from "../store";
import { COLORS, mmss, PHASES } from "../sim";

const pad = (n: number) => String(((n % 60) + 60) % 60).padStart(2, "0");
const zoneColor = (z: string) => (z === "Z3" ? COLORS.lime : z === "Z4" ? COLORS.warn : z === "Z5" ? COLORS.hot : COLORS.teal);

export function SessionPlayer() {
  const { state, actions } = usePerfLab();
  if (!state.sessOpen) return null;

  const phases = PHASES;
  const pIdx = state.phaseIdx;
  const ph = phases[pIdx] || { name: "", zone: "", pace: "", dur: 0 };
  const rem = state.sessRemaining;
  const running = state.sessRunning;
  const done = state.sessDone;

  const phPct = ph.dur ? Math.round(((ph.dur - rem) / ph.dur) * 100) : 0;
  const totalDur = phases.reduce((a, b) => a + b.dur, 0);
  let elapsed = 0;
  for (let i = 0; i < pIdx; i++) elapsed += phases[i].dur;
  elapsed += ph.dur - rem;
  const overallPct = totalDur ? Math.round((elapsed / totalDur) * 100) : 0;
  const workTotal = phases.filter((p) => p.rep).length;
  const phZoneColor = zoneColor(ph.zone);
  const sessPhaseName = ph.name + (ph.rep ? ` · ${ph.rep} of ${workTotal}` : "");
  const sessBtnLabel = running ? "Pause" : done ? "Restart" : elapsed > 0 ? "Resume" : "Start";

  const zoneHR: Record<string, number> = { Z1: 128, Z2: 150, Z3: 168, Z4: 179, Z5: 188 };
  const baseHR = zoneHR[ph.zone] || 140;
  const hrOsc = Math.sin(elapsed / 6) * 2.4 + Math.sin(elapsed / 2.1) * 1.3;
  const liveHR = running ? Math.round(baseHR + hrOsc + (phPct / 100) * 5) : done ? 96 : Math.round(baseHR * 0.6);
  const hrPct = Math.max(2, Math.min(100, Math.round(((liveHR - 110) / (195 - 110)) * 100)));
  const pm = (ph.pace || "").match(/(\d+):(\d+)/);
  const tgtSec = pm ? +pm[1] * 60 + +pm[2] : 0;
  const paceDev = running ? Math.round(Math.sin(elapsed / 4.5) * 4 + Math.cos(elapsed / 2.7) * 2) : 0;
  const liveSec = tgtSec + paceDev;
  const livePace = tgtSec ? `${Math.floor(liveSec / 60)}:${pad(liveSec % 60)}` : "—";
  let paceCue = "On target";
  let paceCueColor: string = COLORS.good;
  if (running && paceDev <= -4) { paceCue = "Too hot · ease off"; paceCueColor = COLORS.hot; }
  else if (running && paceDev >= 5) { paceCue = "Behind · pick it up"; paceCueColor = COLORS.warn; }
  else if (!running) { paceCue = done ? "Complete" : "Paused"; paceCueColor = "#7c818c"; }
  const paceDevStr = `${paceDev > 0 ? "+" : ""}${paceDev}s`;
  const accLoad = Math.round(61 * (elapsed / (totalDur || 1)));
  const loadPct = Math.round((elapsed / (totalDur || 1)) * 100);

  const splits: { name: string; pace: string; dev: string; devColor: string }[] = [];
  phases.forEach((p, i) => {
    if (p.rep && i < pIdx) {
      const dv = ((i * 7) % 9) - 4;
      const sec = tgtSec + dv;
      splits.push({ name: `Interval ${p.rep}`, pace: `${Math.floor(sec / 60)}:${pad(sec % 60)} /km`, dev: `${dv > 0 ? "+" : ""}${dv}s`, devColor: dv <= -3 ? COLORS.hot : dv >= 4 ? COLORS.warn : COLORS.good });
    }
  });

  return (
    <div className="fixed inset-0 z-[70] flex flex-col" style={{ background: "radial-gradient(120% 90% at 50% -10%,#10161b,#06070a 60%)" }}>
      <div className="flex flex-none items-center justify-between border-b border-white/[0.06] px-7 py-5">
        <div className="flex items-center gap-[11px]">
          <span className="h-2 w-2 animate-pl-pulse rounded-full bg-ac" />
          <div>
            <div className="text-[16px] font-bold leading-none text-ink">Tempo intervals</div>
            <div className="mt-[5px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">Live session · Zone 3</div>
          </div>
        </div>
        <button onClick={actions.closeSession} className="rounded-[10px] border border-white/10 bg-white/[0.04] px-[14px] py-[10px] text-[12px] font-semibold leading-none text-mute">End session ✕</button>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1fr_320px]">
        <div className="flex flex-col items-center justify-center gap-6 p-[30px]">
          <div className="text-center">
            <div className="font-mono text-[12px] font-semibold uppercase leading-none tracking-[0.16em]" style={{ color: phZoneColor }}>{sessPhaseName}</div>
            <div className="mt-4 font-mono text-[124px] font-semibold leading-[0.9] tracking-[-0.03em] text-ink">{mmss(rem)}</div>
            <div className="mt-[10px] text-[14px] font-medium leading-none text-[#7c818c]">target {ph.pace} · {ph.zone}</div>
          </div>
          <div className="h-2 w-[min(520px,82%)] overflow-hidden rounded-full bg-white/[0.08]"><div className="h-full rounded-full transition-[width] duration-1000 ease-linear" style={{ width: `${phPct}%`, background: phZoneColor }} /></div>
          <div className="flex gap-3">
            <button onClick={actions.sessToggle} className="min-w-[150px] rounded-[12px] bg-gradient-to-r from-ac to-[#a7e36e] px-10 py-[15px] text-[14px] font-bold leading-none text-[#0a0c10]">{sessBtnLabel}</button>
            <button onClick={actions.sessSkip} className="rounded-[12px] border border-white/[0.12] bg-white/[0.04] px-[22px] py-[15px] text-[14px] font-semibold leading-none text-soft">Skip ›</button>
          </div>
          <div className="mt-[6px] grid w-[min(640px,94%)] grid-cols-1 gap-[14px] sm:grid-cols-3">
            <div className="rounded-[14px] border border-white/[0.07] bg-white/[0.025] p-4">
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">Heart rate</div>
              <div className="mt-[11px] flex items-baseline gap-[5px]"><span className="font-mono text-[30px] font-semibold leading-none text-hot">{liveHR}</span><span className="text-[11px] font-medium leading-none text-faint">bpm</span></div>
              <div className="relative mt-[13px] h-[5px] rounded-full" style={{ background: "linear-gradient(90deg,#7bd6c0,#c6f135,#f5c451,#ff8a5c)" }}><div className="absolute top-[-3px] h-[11px] w-[3px] -translate-x-1/2 rounded-[2px] bg-white shadow-[0_0_6px_rgba(0,0,0,.7)]" style={{ left: `${hrPct}%` }} /></div>
            </div>
            <div className="rounded-[14px] border border-white/[0.07] bg-white/[0.025] p-4">
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">Current pace</div>
              <div className="mt-[11px] flex items-baseline gap-[6px]"><span className="font-mono text-[30px] font-semibold leading-none text-ink">{livePace}</span><span className="text-[11px] font-medium leading-none text-faint">/km · {paceDevStr}</span></div>
              <div className="mt-[13px] text-[11px] font-semibold leading-none" style={{ color: paceCueColor }}>{paceCue}</div>
            </div>
            <div className="rounded-[14px] border border-white/[0.07] bg-white/[0.025] p-4">
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">Session load</div>
              <div className="mt-[11px] flex items-baseline gap-[5px]"><span className="font-mono text-[30px] font-semibold leading-none text-ac">{accLoad}</span><span className="text-[11px] font-medium leading-none text-faint">/ 61 est</span></div>
              <div className="mt-[13px] h-[5px] overflow-hidden rounded-full bg-white/[0.08]"><div className="h-full rounded-full bg-ac transition-[width] duration-1000 ease-linear" style={{ width: `${loadPct}%` }} /></div>
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-[18px] overflow-auto border-l border-white/[0.06] bg-[rgba(8,10,14,.5)] px-5 py-[22px]">
          <div>
            <div className="mb-[11px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-dim">Plan</div>
            <div className="flex flex-col gap-[6px]">
              {phases.map((p, i) => {
                const dot = i < pIdx ? COLORS.good : i === pIdx ? COLORS.lime : "rgba(255,255,255,.15)";
                const current = i === pIdx;
                return (
                  <div key={i} className={`flex items-center gap-[10px] rounded-[9px] border px-[11px] py-[9px] ${current ? "border-ac/20 bg-ac/[0.06]" : "border-white/[0.06]"}`}>
                    <span className="h-[7px] w-[7px] flex-none rounded-full" style={{ background: dot }} />
                    <span className="flex-1 text-[12px] font-medium leading-none text-soft">{p.name}</span>
                    <span className="font-mono text-[11px] font-semibold leading-none text-[#7c818c]">{mmss(p.dur)}</span>
                  </div>
                );
              })}
            </div>
          </div>
          <div>
            <div className="mb-[11px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-dim">Splits</div>
            {splits.length === 0 && <div className="px-[2px] py-1 text-[12px] font-medium leading-[1.5] text-dim">Completed intervals appear here as you finish them.</div>}
            <div className="flex flex-col gap-[6px]">
              {splits.map((sp, i) => (
                <div key={i} className="flex items-center gap-[10px] rounded-[9px] border border-white/[0.06] px-[11px] py-[9px]">
                  <span className="flex-1 text-[12px] font-medium leading-none text-soft">{sp.name}</span>
                  <span className="font-mono text-[11px] font-semibold leading-none text-mute">{sp.pace}</span>
                  <span className="min-w-[30px] text-right font-mono text-[11px] font-semibold leading-none" style={{ color: sp.devColor }}>{sp.dev}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="flex flex-none items-center gap-4 border-t border-white/[0.06] bg-[rgba(8,10,14,.5)] px-7 py-4">
        <span className="flex-none font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#7c818c]">Session</span>
        <div className="h-[6px] flex-1 overflow-hidden rounded-full bg-white/[0.08]"><div className="h-full rounded-full" style={{ width: `${overallPct}%`, background: "linear-gradient(90deg,var(--ac),#7bd6c0)" }} /></div>
        {done && <button onClick={actions.sessToLog} className="flex-none rounded-[10px] bg-gradient-to-r from-mint to-teal px-[18px] py-[11px] text-[13px] font-semibold leading-none text-[#0a0c10]">Log it →</button>}
      </div>
    </div>
  );
}
