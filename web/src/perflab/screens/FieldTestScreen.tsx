// src/perflab/screens/FieldTestScreen.tsx
import { useState } from "react";
import type { ReactNode } from "react";
import { usePerfLab } from "../store";
import { Card, Pill, ScreenHeader, SectionLabel, Tile } from "../ui";
import { computeMetrics } from "@/api/perfLabClient";
import type { ApiError, MetricsResponse } from "@/types";
import { fmtPace, paceLabel } from "@/lib/units";

const inputCls = "mt-2 w-full rounded-[11px] border border-white/10 bg-panel px-[14px] py-3 text-[15px] text-ink";
const ZONE_COLOR = ["text-info", "text-teal", "text-ac", "text-warn", "text-hot"];
const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

export function FieldTestScreen() {
  const { state, actions } = usePerfLab();
  const units = state.settings.units;
  const [t300, setT300] = useState("0:52");
  const [t15, setT15] = useState("9:18");
  const [age, setAge] = useState("28");
  const [sex, setSex] = useState("Female");
  // Seed from the cached result so a prior field test survives navigation.
  const [result, setResult] = useState<MetricsResponse | null>(state.fieldTest);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function compute() {
    setLoading(true);
    setError(null);
    try {
      const r = await computeMetrics({ age: Number(age) || 0, sex, time_300m: t300, time_1p5mi: t15 });
      setResult(r);
      actions.ftCompute(r);
    } catch (e) {
      setError((e as ApiError)?.message ?? "Compute failed — check the backend is reachable.");
    } finally {
      setLoading(false);
    }
  }
  function rerun() {
    setResult(null);
    setError(null);
    actions.ftRecompute();
  }

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
            <label className="block"><span className="text-[12px] font-medium leading-none text-mute">300 m time</span>
              <input value={t300} onChange={(e) => setT300(e.target.value)} placeholder="M:SS" className={`${inputCls} font-mono`} /></label>
            <label className="block"><span className="text-[12px] font-medium leading-none text-mute">1.5 mi time</span>
              <input value={t15} onChange={(e) => setT15(e.target.value)} placeholder="MM:SS" className={`${inputCls} font-mono`} /></label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Age</span>
                <input value={age} onChange={(e) => setAge(e.target.value)} inputMode="numeric" className={`${inputCls} font-mono`} /></label>
              <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Sex</span>
                <select value={sex} onChange={(e) => setSex(e.target.value)} className={inputCls} style={{ colorScheme: "dark" }}>
                  <option>Female</option><option>Male</option>
                </select></label>
            </div>
            <div className="flex items-center gap-[9px] rounded-[11px] border border-info/[0.18] bg-info/[0.06] px-3 py-[10px]">
              <span className="text-[13px] text-info">ⓘ</span>
              <span className="text-[11.5px] font-medium leading-[1.45] text-mute">VO₂ ignores age &amp; sex in v0.3 — captured for upcoming versions.</span>
            </div>
            <button onClick={compute} disabled={loading} className="rounded-[11px] bg-gradient-to-r from-ac to-[#a7e36e] p-[14px] text-[13.5px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60">
              {loading ? "Computing…" : "Compute metrics"}
            </button>
          </div>
        </Card>

        {loading ? (
          <PlaceholderBox>Computing metrics…</PlaceholderBox>
        ) : error ? (
          <div className="flex min-h-[360px] flex-col items-center justify-center gap-3 rounded-[18px] border border-hot/[0.3] bg-hot/[0.05] p-[30px] text-center">
            <div className="grid h-[46px] w-[46px] place-items-center rounded-[12px] border border-hot/[0.3] text-[20px] text-hot">!</div>
            <div className="text-[15px] font-semibold leading-none text-soft">Couldn't compute metrics</div>
            <div className="max-w-[320px] text-[12.5px] font-medium leading-[1.5] text-[#7c818c]">{error}</div>
            <button onClick={compute} className="mt-1 rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[10px] text-[12px] font-semibold leading-none text-soft">Try again</button>
          </div>
        ) : !result ? (
          <PlaceholderBox>Enter your two timed runs and compute to see VO₂, profile and pace zones.</PlaceholderBox>
        ) : (
          <div className="flex flex-col gap-[14px]">
            <div className="grid grid-cols-1 gap-[14px] sm:grid-cols-2">
              <div onClick={() => actions.openExplain("FT:vo2")} className="cursor-pointer rounded-[18px] border border-mint/[0.18] p-5" style={{ background: "linear-gradient(120deg,#0f1f1c,#111419 60%)" }}>
                <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#9ad6c8]">VO₂max</div>
                <div className="mt-3 flex items-end gap-2">
                  <span className="font-mono text-[44px] font-semibold leading-none text-ink">{result.vo2_max.toFixed(1)}</span>
                  <span className="mb-[6px] text-[11px] font-medium leading-none text-[#7c818c]">ml·kg⁻¹·min⁻¹</span>
                </div>
                <div className="mt-3 text-[11.5px] font-medium leading-[1.5] text-[#7c818c]">{result.vo2_category} · estimated from the 1.5 mi split.</div>
              </div>
              <Card onClick={() => actions.openExplain("FT:profile")}>
                <SectionLabel className="text-faint">Speed ↔ Endurance</SectionLabel>
                <div className="mt-3 flex items-end gap-2">
                  <span className="font-mono text-[44px] font-semibold leading-none text-info">{result.fatigue_percent.toFixed(1)}</span>
                  <span className="mb-[6px] text-[11px] font-semibold leading-none text-info">{result.fatigue_percent <= 0 ? "endurance-biased" : "speed-biased"}</span>
                </div>
                <div className="relative mt-[14px] h-[6px] rounded-full" style={{ background: "linear-gradient(90deg,#86b8ff,rgba(255,255,255,.08) 50%,#ff8a5c)" }}>
                  <div className="absolute top-[-3px] h-[12px] w-[3px] rounded-[2px] bg-ink" style={{ left: `${clamp(50 + result.fatigue_percent, 4, 96)}%` }} />
                </div>
                <div className="mt-[7px] flex justify-between font-mono text-[10px] leading-none text-dim"><span>endurance</span><span>speed</span></div>
              </Card>
            </div>

            <Card className="p-5">
              <div className="mb-4 flex items-center justify-between">
                <SectionLabel>Pace zones</SectionLabel>
                <span className="text-[11px] font-medium leading-none text-dim">{paceLabel(units)}</span>
              </div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                {result.zones.slice(0, 5).map((z, i) => (
                  <Tile key={z.name} className="bg-white/[0.03] p-[13px]">
                    <div className={`font-mono text-[11px] font-semibold leading-none ${ZONE_COLOR[i] ?? "text-mute"}`}>Z{i + 1}</div>
                    <div className="mt-[9px] font-mono text-[17px] font-semibold leading-none text-ink">{fmtPace((z.slow_pace_sec + z.fast_pace_sec) / 2, units)}</div>
                    <div className="mt-[5px] text-[10px] font-medium leading-[1.3] text-faint">{z.name}</div>
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
                <button onClick={rerun} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-[14px] py-[10px] text-[12px] font-semibold leading-none text-soft">Re-run</button>
                <button onClick={actions.seedTwin} className="rounded-[9px] bg-gradient-to-r from-mint to-teal px-4 py-[10px] text-[12px] font-semibold leading-none text-[#0a0c10]">Send to Twin →</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function PlaceholderBox({ children }: { children: ReactNode }) {
  return (
    <div
      className="flex min-h-[360px] flex-col items-center justify-center gap-3 rounded-[18px] border border-dashed border-white/10 p-[30px] text-center"
      style={{ background: "repeating-linear-gradient(135deg,#0e1116,#0e1116 14px,#10131a 14px,#10131a 28px)" }}
    >
      <div className="grid h-[46px] w-[46px] place-items-center rounded-[12px] border border-white/[0.12]">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#7c818c" strokeWidth="1.6"><path d="M3 12h4l3 8 4-16 3 8h4" /></svg>
      </div>
      <div className="text-[15px] font-semibold leading-none text-soft">No results yet</div>
      <div className="max-w-[280px] text-[12.5px] font-medium leading-[1.5] text-[#7c818c]">{children}</div>
    </div>
  );
}
