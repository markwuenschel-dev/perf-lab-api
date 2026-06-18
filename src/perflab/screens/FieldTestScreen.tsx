// src/perflab/screens/FieldTestScreen.tsx
import type { InputHTMLAttributes } from "react";
import { usePerfLab } from "../store";
import { Card, Pill, ScreenHeader, SectionLabel, Tile } from "../ui";

const inputCls =
  "mt-2 w-full rounded-[11px] border border-white/10 bg-panel px-[14px] py-3 text-[15px] text-ink";

function Field({ label, mono, ...rest }: { label: string; mono?: boolean } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block">
      <span className="text-[12px] font-medium leading-none text-mute">{label}</span>
      <input {...rest} className={`${inputCls} ${mono ? "font-mono" : ""}`} />
    </label>
  );
}

const ZONES: [string, string, string, string][] = [
  ["Z1", "5:40", "recovery", "text-info"],
  ["Z2", "5:05", "endurance", "text-teal"],
  ["Z3", "4:30", "tempo", "text-ac"],
  ["Z4", "4:10", "threshold", "text-warn"],
  ["Z5", "3:45", "VO₂max", "text-hot"],
];

export function FieldTestScreen() {
  const { state, actions } = usePerfLab();
  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader
        title="Field Test"
        badge={<Pill>/compute-metrics</Pill>}
        subtitle="Two timed runs → an aerobic snapshot, a speed↔endurance profile and pace zones. One-shot — it doesn't evolve until you push it to the twin."
      />

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[380px_1fr]">
        <Card className="p-[22px]">
          <SectionLabel className="mb-[18px]">Test inputs</SectionLabel>
          <div className="flex flex-col gap-4">
            <Field label="300 m time" defaultValue="0:52" placeholder="M:SS" mono />
            <Field label="1.5 mi time" defaultValue="9:18" placeholder="MM:SS" mono />
            <div className="grid grid-cols-2 gap-3">
              <Field label="Age" defaultValue="28" mono />
              <label className="block">
                <span className="text-[12px] font-medium leading-none text-mute">Sex</span>
                <select defaultValue="Female" className={inputCls} style={{ colorScheme: "dark" }}>
                  <option>Female</option>
                  <option>Male</option>
                </select>
              </label>
            </div>
            <div className="flex items-center gap-[9px] rounded-[11px] border border-info/[0.18] bg-info/[0.06] px-3 py-[10px]">
              <span className="text-[13px] text-info">ⓘ</span>
              <span className="text-[11.5px] font-medium leading-[1.45] text-mute">VO₂ ignores age &amp; sex in v0.3 — captured for upcoming versions.</span>
            </div>
            <button onClick={actions.ftCompute} className="rounded-[11px] bg-gradient-to-r from-ac to-[#a7e36e] p-[14px] text-[13.5px] font-semibold leading-none text-[#0a0c10]">
              Compute metrics
            </button>
          </div>
        </Card>

        {!state.ftDone ? (
          <div
            className="flex min-h-[360px] flex-col items-center justify-center gap-3 rounded-[18px] border border-dashed border-white/10 p-[30px] text-center"
            style={{ background: "repeating-linear-gradient(135deg,#0e1116,#0e1116 14px,#10131a 14px,#10131a 28px)" }}
          >
            <div className="grid h-[46px] w-[46px] place-items-center rounded-[12px] border border-white/[0.12]">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#7c818c" strokeWidth="1.6"><path d="M3 12h4l3 8 4-16 3 8h4" /></svg>
            </div>
            <div className="text-[15px] font-semibold leading-none text-soft">No results yet</div>
            <div className="max-w-[280px] text-[12.5px] font-medium leading-[1.5] text-[#7c818c]">Enter your two timed runs and compute to see VO₂, profile and pace zones.</div>
          </div>
        ) : (
          <div className="flex flex-col gap-[14px]">
            <div className="grid grid-cols-1 gap-[14px] sm:grid-cols-2">
              <div onClick={() => actions.openExplain("FT:vo2")} className="cursor-pointer rounded-[18px] border border-mint/[0.18] p-5" style={{ background: "linear-gradient(120deg,#0f1f1c,#111419 60%)" }}>
                <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#9ad6c8]">VO₂max</div>
                <div className="mt-3 flex items-end gap-2">
                  <span className="font-mono text-[44px] font-semibold leading-none text-ink">58.4</span>
                  <span className="mb-[6px] text-[11px] font-medium leading-none text-[#7c818c]">ml·kg⁻¹·min⁻¹</span>
                </div>
                <div className="mt-3 text-[11.5px] font-medium leading-[1.5] text-[#7c818c]">Estimated aerobic ceiling from the 1.5 mi split.</div>
              </div>
              <Card onClick={() => actions.openExplain("FT:profile")}>
                <SectionLabel className="text-faint">Speed ↔ Endurance</SectionLabel>
                <div className="mt-3 flex items-end gap-2">
                  <span className="font-mono text-[44px] font-semibold leading-none text-info">−7.2</span>
                  <span className="mb-[6px] text-[11px] font-semibold leading-none text-info">endurance-biased</span>
                </div>
                <div className="relative mt-[14px] h-[6px] rounded-full" style={{ background: "linear-gradient(90deg,#86b8ff,rgba(255,255,255,.08) 50%,#ff8a5c)" }}>
                  <div className="absolute left-[39%] top-[-3px] h-[12px] w-[3px] rounded-[2px] bg-ink" />
                </div>
                <div className="mt-[7px] flex justify-between font-mono text-[10px] leading-none text-dim"><span>endurance</span><span>speed</span></div>
              </Card>
            </div>

            <Card className="p-5">
              <div className="mb-4 flex items-center justify-between">
                <SectionLabel>Pace zones</SectionLabel>
                <span className="text-[11px] font-medium leading-none text-dim">min/km</span>
              </div>
              <div className="grid grid-cols-5 gap-2">
                {ZONES.map(([z, pace, label, color]) => (
                  <Tile key={z} className="bg-white/[0.03] p-[13px]">
                    <div className={`font-mono text-[11px] font-semibold leading-none ${color}`}>{z}</div>
                    <div className="mt-[9px] font-mono text-[17px] font-semibold leading-none text-ink">{pace}</div>
                    <div className="mt-[5px] text-[10px] font-medium leading-none text-faint">{label}</div>
                  </Tile>
                ))}
              </div>
            </Card>

            <div className="flex items-center justify-between gap-[14px] rounded-[18px] border border-mint/[0.18] px-5 py-4" style={{ background: "linear-gradient(120deg,#0f1f1c,#111419 60%)" }}>
              <div>
                <div className="text-[13px] font-semibold leading-none text-ink">Seed your digital twin</div>
                <div className="mt-[5px] text-[11.5px] font-medium leading-[1.5] text-[#7c818c]">Push this snapshot into S(t) as the new baseline.</div>
              </div>
              <div className="flex flex-none gap-[9px]">
                <button onClick={actions.ftRecompute} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-[14px] py-[10px] text-[12px] font-semibold leading-none text-soft">Re-run</button>
                <button onClick={actions.seedTwin} className="rounded-[9px] bg-gradient-to-r from-mint to-teal px-4 py-[10px] text-[12px] font-semibold leading-none text-[#0a0c10]">Send to Twin →</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
