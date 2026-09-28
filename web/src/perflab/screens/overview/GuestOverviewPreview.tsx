// src/perflab/screens/overview/GuestOverviewPreview.tsx
//
// The GUEST Overview: a clearly-labelled preview of a sample athlete, driven by
// the deterministic sim (sim.ts). This is the only Overview path that renders
// fixture numbers, and it says so continuously rather than once.
//
// CURATED, not a mirror (map #182, #185 · option A′). Guests land here first
// (LoginScreen -> App -> store's initial screen), so a sign-in wall would be the
// first thing a visitor sees — but a full fixture-backed copy of the authenticated
// screen would mean maintaining a second fake implementation of every section.
// Four sections are previewed: readiness, the trend, the morning metrics, and the
// twin snapshot. Objectives, today's session, recent activity, training load,
// habit and insights are NOT faked here — they get sign-in prompts instead.
import { usePerfLab } from "../../store";
import { Card, ReadinessRing, SectionLabel } from "../../ui";
import { Sparkline } from "../../viz";
import { DAYS } from "../../sim";
import { readinessColor, readinessNote, readinessWord } from "../../readinessPresentation";
import { OverviewHeader, Snap } from "./overviewLeaves";
import { TREND_LABEL } from "./overviewModel";
import { SampleTag } from "../../SampleTag";

// The provenance label now lives in its own neutral module so guest surfaces
// outside this file (the sidebar block card) can carry an identical badge
// without importing a fixture module.

export function GuestOverviewPreview() {
  const { actions } = usePerfLab();
  const today = DAYS[DAYS.length - 1];
  const series = DAYS.slice(Math.max(0, DAYS.length - 14)).map((d) => d.readiness);
  const value = today.readiness;
  const color = readinessColor(value);
  const word = readinessWord(value);
  const delta = series[series.length - 1] - series[0];

  const meanFatigue = Math.round(Object.values(today.F).reduce((a, b) => a + b, 0) / 6);
  const [peakRegion, peakValue] = Object.entries(today.T).sort((a, b) => b[1] - a[1])[0];

  return (
    <>
      <OverviewHeader
        name="Guest"
        actions={
          <button onClick={actions.openAuth} className="rounded-[9px] bg-ac px-[15px] py-[9px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Sign in</button>
        }
      />

      <div className="flex items-center gap-2 rounded-[12px] border border-mint/25 bg-mint/[0.08] px-4 py-[10px] text-[12px] font-medium leading-none text-[#9ad6c8]">
        <span className="h-[7px] w-[7px] flex-none rounded-full bg-ac" />
        Preview — sample athlete. Sign in to see your own live data.
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
        <Card className="p-6">
          <div className="flex items-center gap-4">
            <ReadinessRing value={value} color={color} size={96} inner={74} valueClassName="text-[29px]" />
            <div>
              <div className="flex items-center gap-2">
                <SectionLabel className="text-faint">Readiness</SectionLabel>
                <SampleTag />
              </div>
              <div className="mt-2 text-[17px] font-bold leading-none" style={{ color }}>{word}</div>
              <div className="mt-[9px] max-w-[320px] text-[11.5px] font-medium leading-[1.5] text-mute">
                {readinessNote(value)}
              </div>
            </div>
          </div>

          {/* The trend is a separate block with its own label and its own sample
              tag — it is fatigue-derived, not the readiness number above it (#186). */}
          <div className="mt-5 border-t border-white/[0.06] pt-4">
            <div className="flex items-center justify-between">
              <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.14em] text-dim">
                {TREND_LABEL} · last 14 days
              </div>
              <SampleTag />
            </div>
            <div className="mt-[7px] text-[11px] font-medium leading-none text-good">
              {delta >= 0 ? "+" : ""}{delta} vs 2w ago
            </div>
            <Sparkline values={series} min={20} max={100} width={300} height={70} className="mt-2 block h-[56px] w-full" />
          </div>
        </Card>

        <Card className="flex flex-col gap-[14px]">
          <div className="flex items-center justify-between">
            <SectionLabel>This morning</SectionLabel>
            <SampleTag />
          </div>
          <div className="grid grid-cols-2 gap-3">
            {[
              ["HRV", "64 ms"],
              ["Sleep", "7.5 h"],
              ["Rest HR", "52 bpm"],
              ["Soreness", "Mild"],
            ].map(([l, v]) => (
              <div key={l}>
                <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.1em] text-faint">{l}</div>
                <div className="mt-[6px] font-mono text-[15px] font-semibold leading-none text-ink">{v}</div>
              </div>
            ))}
          </div>
          <div className="text-[10.5px] font-medium leading-none text-dim">Sign in to log and track your own.</div>
        </Card>
      </div>

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <SectionLabel>Twin snapshot</SectionLabel>
            <SampleTag />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-x-[22px] gap-y-[14px] lg:grid-cols-4">
          <Snap label="Aerobic" value={Math.round(today.C.aerobic)} />
          <Snap label="Strength" value={Math.round(today.C.strength)} color="text-teal" />
          <Snap label="Mean fatigue" value={meanFatigue} color="text-warn" />
          <Snap
            label="Peak tissue"
            value={<>{peakValue} <span className="text-[11px] text-faint">{peakRegion.toLowerCase()}</span></>}
            color="text-warn"
          />
        </div>
      </Card>
    </>
  );
}
