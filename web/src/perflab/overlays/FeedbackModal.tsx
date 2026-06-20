// src/perflab/overlays/FeedbackModal.tsx
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { usePerfLab } from "../store";
import type { Feel } from "../store";
import { COLORS, projectLogDose } from "../sim";
import { CloseBtn } from "./LogWorkoutModal";

const STATS: [string, ReactNode, string][] = [
  ["Distance", "9.1 km", "text-ink"],
  ["Duration", "53:20", "text-ink"],
  ["Avg pace", <>4:32<span className="text-[10px] text-faint"> /km</span></>, "text-ink"],
  ["Avg HR", <>168<span className="text-[10px] text-faint"> bpm</span></>, "text-hot"],
];

const FEELS: [Feel, string, number][] = [
  ["easy", "Easy", 4],
  ["controlled", "Controlled", 6],
  ["hard", "Hard", 8],
  ["maxed", "Maxed", 10],
];

export function FeedbackModal() {
  const { state, actions } = usePerfLab();
  if (!state.feedbackOpen) return null;
  const { readyAfter, fatAfter, capDelta, cap, readyColor } = projectLogDose(state);

  return (
    <div className="fixed inset-0 z-[63] flex items-center justify-center p-8 backdrop-blur-[5px]" style={{ background: "rgba(4,5,8,.72)" }}>
      <div className="max-h-[92vh] w-[720px] max-w-full overflow-auto rounded-[18px] border border-white/[0.09] bg-surface shadow-[0_50px_110px_-30px_rgba(0,0,0,.78)]">
        {!state.feedbackApplied ? (
          <div>
            <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-5">
              <div className="flex items-center gap-[11px]">
                <span className="h-2 w-2 rounded-full bg-good" />
                <div>
                  <h2 className="m-0 text-[18px] font-bold leading-none text-ink">Session complete</h2>
                  <div className="mt-[6px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">Tempo intervals · Zone 3</div>
                </div>
              </div>
              <CloseBtn onClick={actions.closeFeedback} />
            </div>
            <div className="flex flex-col gap-[22px] p-6">
              <div className="grid grid-cols-2 gap-[14px] sm:grid-cols-4">
                {STATS.map(([label, value, color]) => (
                  <div key={label} className="rounded-[13px] border border-white/[0.06] bg-tile p-[14px]">
                    <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">{label}</div>
                    <div className={`mt-[9px] font-mono text-[19px] font-semibold leading-none ${color}`}>{value}</div>
                  </div>
                ))}
              </div>
              <div>
                <div className="mb-3 font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">How did it feel?</div>
                <div className="flex gap-2">
                  {FEELS.map(([key, label, rpe]) => (
                    <div
                      key={key}
                      onClick={() => actions.setFeel(key, rpe)}
                      className={cn("flex-1 cursor-pointer rounded-[9px] border px-[6px] py-[10px] text-center text-[12px] font-semibold leading-none", state.feel === key ? "border-ac/40 bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute")}
                    >
                      {label}
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <div className="mb-[10px] flex items-center justify-between">
                  <span className="font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">Perceived effort</span>
                  <span className="font-mono text-[14px] font-semibold leading-none text-ac">{state.rpe} <span className="text-[11px] text-dim">/ 10 RPE</span></span>
                </div>
                <input type="range" min={1} max={10} value={state.rpe} onChange={(e) => actions.setRpe(+e.target.value)} className="w-full cursor-pointer" style={{ accentColor: "var(--ac)" }} />
              </div>
              <div className="rounded-[14px] border border-white/[0.07] bg-white/[0.02] px-[18px] py-4">
                <div className="mb-[14px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">How your twin will update</div>
                <div className="grid grid-cols-3 gap-4">
                  <TwinDelta label="Readiness" from="64" to={`${readyAfter}`} toColor={readyColor} />
                  <TwinDelta label="Mean fatigue" from="33" to={`${fatAfter}`} toColor={COLORS.hot} />
                  <TwinDelta label={cap} drive to={capDelta} toColor={COLORS.teal} />
                </div>
              </div>
            </div>
            <div className="flex items-center justify-end gap-[9px] border-t border-white/[0.06] px-6 py-4">
              <button onClick={actions.closeFeedback} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft">Discard</button>
              <button onClick={actions.applyFeedback} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-[18px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Update my twin →</button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-4 px-9 py-10 text-center">
            <div className="grid h-[62px] w-[62px] place-items-center rounded-full border border-good/35 bg-good/[0.12]">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#5fd08a" strokeWidth="2.2"><path d="M20 6 9 17l-5-5" /></svg>
            </div>
            <div className="text-[22px] font-bold leading-[1.2] text-ink">Twin updated</div>
            <div className="max-w-[380px] text-[13.5px] font-medium leading-[1.6] text-[#7c818c]">Your session was folded into S(t). Fatigue and capacity advanced; tomorrow's recommendation is recomputed against the new state.</div>
            <div className="mt-[6px] grid w-full max-w-[440px] grid-cols-3 gap-[14px]">
              <ResultCard label="Readiness" value={`64 → ${readyAfter}`} color={readyColor} />
              <ResultCard label="Fatigue" value={`33 → ${fatAfter}`} color={COLORS.hot} />
              <ResultCard label={cap} value={capDelta} color={COLORS.teal} />
            </div>
            <div className="mt-[10px] flex gap-[10px]">
              <button onClick={actions.closeFeedback} className="rounded-[10px] border border-white/10 bg-white/[0.04] px-[18px] py-3 text-[12.5px] font-semibold leading-none text-soft">Done</button>
              <button onClick={actions.feedbackToTwin} className="rounded-[10px] bg-gradient-to-r from-mint to-teal px-5 py-3 text-[12.5px] font-semibold leading-none text-[#0a0c10]">View twin →</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function TwinDelta({ label, from, to, toColor, drive }: { label: string; from?: string; to: string; toColor: string; drive?: boolean }) {
  return (
    <div>
      <div className="text-[11px] font-medium leading-none text-mute">{label}</div>
      <div className="mt-2 flex items-baseline gap-[6px]">
        <span className="font-mono text-[17px] font-semibold leading-none text-soft">{drive ? "drive" : from}</span>
        {!drive && <span className="text-[12px] font-medium leading-none text-dim">→</span>}
        <span className="font-mono text-[17px] font-semibold leading-none" style={{ color: toColor }}>{to}</span>
      </div>
    </div>
  );
}

function ResultCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="rounded-[13px] border border-white/[0.06] bg-tile p-[15px]">
      <div className="text-[11px] font-medium leading-none text-mute">{label}</div>
      <div className="mt-2 font-mono text-[18px] font-semibold leading-none" style={{ color }}>{value}</div>
    </div>
  );
}
