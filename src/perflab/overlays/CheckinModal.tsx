// src/perflab/overlays/CheckinModal.tsx
import type { InputHTMLAttributes } from "react";
import { cn } from "@/lib/utils";
import { usePerfLab } from "../store";
import { buildCheckin, readinessColor, readinessWord } from "../sim";
import { ReadinessRing } from "../ui";
import { CloseBtn } from "./LogWorkoutModal";

function Slider({ label, display, ...rest }: { label: string; display: string } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div>
      <div className="mb-[11px] flex items-center justify-between">
        <span className="font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.12em] text-mute">{label}</span>
        <span className="font-mono text-[13px] font-semibold leading-none text-ac">{display}</span>
      </div>
      <input type="range" {...rest} className="w-full cursor-pointer" style={{ accentColor: "var(--ac)" }} />
    </div>
  );
}

const SORE: ["none" | "mild" | "moderate" | "high", string][] = [
  ["none", "None"],
  ["mild", "Mild"],
  ["moderate", "Moderate"],
  ["high", "High"],
];

export function CheckinModal() {
  const { state, actions } = usePerfLab();
  if (!state.checkinOpen) return null;
  const ci = state.checkin;
  const r = buildCheckin(ci);
  const color = readinessColor(r.readiness);

  return (
    <div className="fixed inset-0 z-[62] flex items-center justify-center p-8 backdrop-blur-[4px]" style={{ background: "rgba(4,5,8,.7)" }}>
      <div className="max-h-[92vh] w-[840px] max-w-full overflow-auto rounded-[18px] border border-white/[0.09] bg-surface shadow-[0_50px_110px_-30px_rgba(0,0,0,.75)]">
        <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-5">
          <div className="flex items-center gap-[10px]">
            <h2 className="m-0 text-[18px] font-bold leading-none tracking-[-0.01em] text-ink">Morning check-in</h2>
            <span className="rounded-[7px] border border-mint/25 bg-mint/[0.12] px-2 py-[5px] font-mono text-[10px] font-semibold leading-none tracking-[0.1em] text-[#9ad6c8]">readiness inputs</span>
          </div>
          <CloseBtn onClick={actions.closeCheckin} />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-[1fr_300px]">
          <div className="flex flex-col gap-[22px] border-r border-white/[0.06] p-6">
            <Slider label="HRV (overnight)" display={`${ci.hrv} ms`} min={30} max={110} step={1} value={ci.hrv} onChange={(e) => actions.setCheckin({ hrv: +e.target.value })} />
            <Slider label="Sleep duration" display={`${ci.sleepH} h`} min={4} max={10} step={0.5} value={ci.sleepH} onChange={(e) => actions.setCheckin({ sleepH: +e.target.value })} />
            <Slider label="Sleep quality" display={`Quality ${ci.sleepQ}/5`} min={1} max={5} step={1} value={ci.sleepQ} onChange={(e) => actions.setCheckin({ sleepQ: +e.target.value })} />
            <Slider label="Resting HR" display={`${ci.rhr} bpm`} min={38} max={72} step={1} value={ci.rhr} onChange={(e) => actions.setCheckin({ rhr: +e.target.value })} />
            <div>
              <div className="mb-[11px] font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.12em] text-mute">Soreness</div>
              <div className="flex gap-2">
                {SORE.map(([k, label]) => (
                  <div
                    key={k}
                    onClick={() => actions.setCheckin({ soreness: k })}
                    className={cn("flex-1 cursor-pointer rounded-[9px] border px-[6px] py-[10px] text-center text-[12px] font-semibold leading-none", ci.soreness === k ? "border-ac/40 bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute")}
                  >
                    {label}
                  </div>
                ))}
              </div>
            </div>
            <Slider label="Motivation" display={`${ci.mood} / 5`} min={1} max={5} step={1} value={ci.mood} onChange={(e) => actions.setCheckin({ mood: +e.target.value })} />
          </div>

          <div className="flex flex-col items-center gap-[18px] bg-white/[0.015] p-6">
            <div className="self-start font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">Today's readiness</div>
            <ReadinessRing value={r.readiness} color={color} innerClassName="bg-surface" />
            <div className="text-[16px] font-bold leading-none" style={{ color }}>{readinessWord(r.readiness)}</div>
            <div className="flex w-full flex-col gap-[1px] border-t border-white/[0.06] pt-[14px]">
              {r.drivers.map((d) => (
                <div key={d.n} className="flex items-center justify-between gap-[10px] py-2">
                  <div>
                    <div className="text-[12px] font-semibold leading-none text-[#e6e8ec]">{d.n}</div>
                    <div className="mt-1 text-[10.5px] font-medium leading-none text-faint">{d.v}</div>
                  </div>
                  <span className="font-mono text-[13px] font-semibold leading-none" style={{ color: d.color }}>{d.delta}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between gap-[9px] border-t border-white/[0.06] px-6 py-4">
          <span className="max-w-[330px] text-[11px] font-medium leading-[1.4] text-dim">Readiness seeds today's recommended session and your twin's starting state.</span>
          <div className="flex flex-none gap-[9px]">
            <button onClick={actions.closeCheckin} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft">Cancel</button>
            <button onClick={actions.applyCheckin} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-[18px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Set today's readiness →</button>
          </div>
        </div>
      </div>
    </div>
  );
}
