// src/perflab/screens/SimulatorScreen.tsx
//
// Twin Simulator (Phase 7) — a GOAL-AWARE forward projection of the athlete's
// eight capacity axes. The plan controls (goal / volume / intensity / recovery /
// horizon) feed a single projection source; the output shows start → projected
// across all 8 axes, a per-axis trajectory vs a "maintain" baseline, the
// readiness curve and peak fatigue. No running-only vocabulary here.
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import * as api from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import { usePerfLab, TRAINING_GOALS } from "../store";
import { Card, Pill, ScreenHeader, SectionLabel, Tile } from "../ui";
import { Chart, Area, Line, Marker, useVizTheme } from "../viz";
import { COLORS, readinessColor, readinessWord } from "../sim";
import { useAuthedResource } from "../useAuthedResource";
import {
  placeholderProjection,
  dominantAxes,
  goalLabel,
  type ProjectionResponse,
  type ProjectionAxis,
} from "../projection";

const seg = (active: boolean) =>
  cn(
    "flex-1 cursor-pointer rounded-[9px] border px-[6px] py-[10px] text-center text-[12px] font-semibold leading-none transition-colors",
    active ? "border-ac/40 bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute",
  );

const fmtDelta = (n: number) => `${n >= 0 ? "+" : "−"}${Math.abs(Math.round(n))}`;
const fmtPct = (n: number) => `${n >= 0 ? "+" : "−"}${Math.abs(Math.round(n * 100))}%`;
const relGain = (a: ProjectionAxis) => (a.baseline > 0 ? (a.projected - a.baseline) / a.baseline : 0);

export function SimulatorScreen() {
  const { state, actions } = usePerfLab();
  const auth = useAuth();
  const sim = state.sim;

  // Default the goal to the athlete's profile goal once it loads. A manual pick
  // made before the profile arrives is respected (the ref trips only once).
  const seededRef = useRef(false);
  useEffect(() => {
    if (seededRef.current) return;
    const g = auth.profile?.primary_goal;
    if (g) {
      seededRef.current = true;
      if (g !== sim.goal) actions.setSim({ goal: g });
    }
  }, [auth.profile, sim.goal, actions]);

  // ─── SINGLE PROJECTION SOURCE ──────────────────────────────────────────────
  // Signed in → project against the seeded twin via the real endpoint. Guest /
  // signed out → the deterministic local placeholder (the "preview data" pill).
  // The placeholder is also the graceful fallback while the first fetch is in
  // flight (so we never flash an empty screen) and if the fetch errors.
  const placeholder: ProjectionResponse = placeholderProjection({
    goal: sim.goal,
    weeks: sim.weeks,
    weekly_volume: sim.volume,
    intensity: sim.intensity,
    recovery: sim.recovery,
  });
  const {
    data: fetched,
    loading: projLoading,
    error: projError,
  } = useAuthedResource(
    (t) =>
      api.getSimulateProjection(
        {
          goal: sim.goal,
          weeks: sim.weeks,
          weekly_volume: sim.volume,
          intensity: sim.intensity,
          recovery: sim.recovery,
        },
        t,
      ),
    [sim.goal, sim.weeks, sim.volume, sim.intensity, sim.recovery],
  );
  // `fetched` persists across control-change refetches (the hook only clears it
  // on error), so `?? placeholder` keeps the last real projection during a
  // refetch and swaps to the local one only before the first result or on error.
  const proj: ProjectionResponse = fetched ?? placeholder;
  const usingFallback = !!auth.token && !fetched; // signed in but on placeholder
  // ───────────────────────────────────────────────────────────────────────────

  const weeks = proj.weeks;
  const axes = proj.axes;

  // Trajectory: selectable axis, defaulting to the goal's dominant axis.
  const domOrder = dominantAxes(sim.goal);
  const [selKey, setSelKey] = useState<string | null>(null);
  const activeKey = selKey && axes.some((a) => a.key === selKey) ? selKey : domOrder[0];
  const selAxis = axes.find((a) => a.key === activeKey) ?? axes[0];

  // Headline stats.
  const endReady = proj.readiness_series[weeks];
  const rColor = readinessColor(endReady);
  const peakColor = proj.peak_fatigue < 45 ? COLORS.good : proj.peak_fatigue < 65 ? COLORS.warn : COLORS.hot;
  const topAxis = axes.reduce((m, a) => (relGain(a) > relGain(m) ? a : m), axes[0]);
  const avgUplift = axes.reduce((s, a) => s + relGain(a), 0) / axes.length;

  // Trajectory chart y-domain (padded around plan + baseline series).
  const { accent, colors } = useVizTheme();
  const tAll = selAxis.series.concat(selAxis.baseline_series);
  const tHi = Math.max(...tAll), tLo = Math.min(...tAll);
  const tPad = (tHi - tLo) * 0.14 + 0.5;
  const trajData = (s: number[]) => s.map((v, i) => [i, v] as [number, number]);

  // Narrative.
  const advice =
    proj.peak_fatigue >= 65
      ? "Consider more recovery emphasis or trimming volume to keep fatigue in check."
      : proj.peak_fatigue >= 45
        ? "Sustainable alongside built-in down weeks."
        : "Comfortably within a safe load.";
  const narr = `A ${goalLabel(sim.goal)} plan at volume ${sim.volume} · ${sim.intensity} intensity over ${weeks} weeks lifts your ${topAxis.label.toLowerCase()} most (${fmtPct(relGain(topAxis))} vs maintaining), with an average ${fmtPct(avgUplift)} across all eight capacity axes. Readiness settles near ${endReady}/100 and peak fatigue reaches ${proj.peak_fatigue}. ${advice}`;

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader
        title="Twin Simulator"
        badge={<Pill>what-if · X(t) projection</Pill>}
        subtitle="Run your digital twin forward against a goal. Shape the plan on the left and watch all eight capacity axes, readiness and fatigue respond — measured against simply maintaining."
      >
        {!auth.token && <Pill className="border-white/15 bg-white/[0.06] text-mute">preview data</Pill>}
        {auth.token && projLoading && (
          <Pill className="border-ac/25 bg-ac/[0.08] text-ac">projecting…</Pill>
        )}
      </ScreenHeader>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[360px_1fr]">
        {/* ── Controls ── */}
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
            <SectionLabel className="mb-[11px]">Goal</SectionLabel>
            <select
              value={sim.goal}
              onChange={(e) => actions.setSim({ goal: e.target.value })}
              className="w-full cursor-pointer rounded-[10px] border border-white/10 bg-panel px-3 py-[11px] text-[13px] font-semibold leading-none text-soft outline-none focus:border-ac/40"
              style={{ colorScheme: "dark" }}
            >
              {TRAINING_GOALS.map((g) => (
                <option key={g.value} value={g.value}>{g.label}</option>
              ))}
            </select>
            <div className="mt-[7px] font-mono text-[10px] leading-none text-dim">shapes which axes grow</div>
          </div>
          <div>
            <div className="mb-3 flex items-center justify-between">
              <SectionLabel>Weekly volume</SectionLabel>
              <span className="font-mono text-[13px] font-semibold leading-none text-ac">{sim.volume}</span>
            </div>
            <input type="range" min={30} max={90} step={2} value={sim.volume} onChange={(e) => actions.setSim({ volume: +e.target.value })} className="w-full cursor-pointer" style={{ accentColor: "var(--ac)" }} />
            <div className="mt-[6px] flex justify-between font-mono text-[10px] leading-none text-dim"><span>30</span><span>90</span></div>
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
              {[4, 8, 12, 16].map((wk) => (
                <div key={wk} onClick={() => actions.setSim({ weeks: wk })} className={seg(sim.weeks === wk)}>{wk} wk</div>
              ))}
            </div>
          </div>
        </Card>

        {/* ── Output ── */}
        <div className="flex flex-col gap-4">
          {/* Stat tiles */}
          <div className="grid grid-cols-2 gap-[14px] lg:grid-cols-4">
            <Tile className="p-4">
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">End readiness</div>
              <div className="mt-[11px] flex items-baseline gap-[7px]">
                <span className="font-mono text-[26px] font-semibold leading-none" style={{ color: rColor }}>{endReady}</span>
                <span className="text-[12px] font-semibold leading-none" style={{ color: rColor }}>{readinessWord(endReady)}</span>
              </div>
              <div className="mt-2 text-[11px] font-medium leading-none text-faint">at week {weeks}</div>
            </Tile>
            <Tile className="p-4">
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">Peak fatigue</div>
              <div className="mt-[11px] font-mono text-[26px] font-semibold leading-none" style={{ color: peakColor }}>{proj.peak_fatigue}</div>
              <div className="mt-3 h-[5px] overflow-hidden rounded-full bg-white/[0.08]"><div className="h-full rounded-full transition-all" style={{ width: `${proj.peak_fatigue}%`, background: peakColor }} /></div>
            </Tile>
            <Tile className="p-4">
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">Top gain</div>
              <div className="mt-[11px] font-mono text-[19px] font-semibold leading-none text-teal">{topAxis.label}</div>
              <div className="mt-2 text-[11px] font-semibold leading-none text-good">{fmtPct(relGain(topAxis))} vs maintain</div>
            </Tile>
            <Tile className="p-4">
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">Plan uplift</div>
              <div className="mt-[11px] font-mono text-[26px] font-semibold leading-none text-ink">{fmtPct(avgUplift)}</div>
              <div className="mt-2 text-[11px] font-medium leading-none text-faint">avg across 8 axes</div>
            </Tile>
          </div>

          {/* 8-axis start → projected */}
          <Card className="p-5">
            <div className="mb-[18px] flex items-center justify-between">
              <SectionLabel>Capacity projection · X(t)</SectionLabel>
              <div className="flex items-center gap-4">
                <span className="flex items-center gap-[7px] text-[11px] font-medium leading-none text-soft"><span className="h-[6px] w-4 rounded-[2px] bg-ac" />projected</span>
                <span className="flex items-center gap-[7px] text-[11px] font-medium leading-none text-mute"><span className="h-[10px] w-[2px] bg-[#6b7280]" />maintain</span>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-x-6 gap-y-5 md:grid-cols-4">
              {axes.map((a) => {
                const scaleMax = Math.max(a.projected, a.baseline, a.start) * 1.12;
                const fillPct = Math.max(3, Math.min(100, (a.projected / scaleMax) * 100));
                const basePct = Math.max(2, Math.min(100, (a.baseline / scaleMax) * 100));
                const d = a.projected - a.baseline;
                return (
                  <div key={a.key}>
                    <div className="mb-2 text-[12px] font-medium leading-none text-mute">{a.label}</div>
                    <div className="font-mono text-[26px] font-semibold leading-none text-ink">{Math.round(a.projected)}</div>
                    <div className="relative mb-[7px] mt-[11px] h-[6px] overflow-hidden rounded-full bg-white/[0.07]">
                      <div className="h-full rounded-full" style={{ width: `${fillPct}%`, background: "linear-gradient(90deg,var(--ac),#a7e36e)" }} />
                      <div className="absolute top-[-2px] h-[10px] w-[2px] rounded-full bg-[#8b919c]" style={{ left: `calc(${basePct}% - 1px)` }} />
                    </div>
                    <div className="font-mono text-[10px] leading-none" style={{ color: d > 0.5 ? COLORS.good : d < -0.5 ? COLORS.hot : COLORS.dim }}>{fmtDelta(d)} vs maintain</div>
                  </div>
                );
              })}
            </div>
          </Card>

          {/* Trajectory: selectable axis vs maintain */}
          <Card className="p-5">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <SectionLabel>Trajectory · {selAxis.label}</SectionLabel>
              <div className="flex items-center gap-4">
                <span className="flex items-center gap-[7px] text-[11px] font-medium leading-none text-soft"><span className="h-[3px] w-4 rounded-[2px] bg-ac" />This plan</span>
                <span className="flex items-center gap-[7px] text-[11px] font-medium leading-none text-mute"><span className="w-4 border-t-2 border-dashed border-[#5a626e]" />Maintain</span>
              </div>
            </div>
            <div className="mb-3 flex flex-wrap gap-[6px]">
              {axes.map((a) => (
                <button key={a.key} onClick={() => setSelKey(a.key)} className={cn("rounded-[7px] border px-[9px] py-[6px] text-[11px] font-semibold leading-none transition-colors", a.key === activeKey ? "border-ac/40 bg-ac/[0.12] text-ac" : "border-white/10 bg-white/[0.03] text-mute")}>{a.label}</button>
              ))}
            </div>
            <Chart
              width={520}
              height={188}
              padding={{ top: 14, right: 6, bottom: 26, left: 6 }}
              xDomain={[0, weeks]}
              yDomain={[tLo - tPad, tHi + tPad]}
              ariaLabel={`${selAxis.label} trajectory versus maintaining`}
              className="h-[220px] w-full"
            >
              <Line data={trajData(selAxis.baseline_series)} color={colors.text.mute} dashed />
              <Area data={trajData(selAxis.series)} color={accent} />
              <Marker x={weeks} y={selAxis.projected} color={accent} />
            </Chart>
            <div className="mt-1 flex justify-between font-mono text-[10px] leading-none text-dim"><span>now · {Math.round(selAxis.start)}</span><span>{weeks} wk · {Math.round(selAxis.projected)}</span></div>
          </Card>

          {/* Readiness curve */}
          <Card className="p-5">
            <div className="mb-2 flex items-center justify-between">
              <SectionLabel>Readiness under this plan</SectionLabel>
              <span className="font-mono text-[10px] leading-none text-dim">0–100 scale</span>
            </div>
            <Chart
              width={520}
              height={188}
              padding={{ top: 14, right: 6, bottom: 26, left: 6 }}
              xDomain={[0, weeks]}
              yDomain={[20, 100]}
              ariaLabel="Readiness under this plan"
              className="h-[180px] w-full"
            >
              <Area data={proj.readiness_series.map((v, i) => [i, v] as [number, number])} color={COLORS.teal} />
              <Marker x={weeks} y={endReady} color={COLORS.teal} />
            </Chart>
            <div className="mt-1 flex justify-between font-mono text-[10px] leading-none text-dim"><span>now</span><span>{weeks} wk · {endReady}</span></div>
          </Card>

          {/* Narrative */}
          <Card className="flex items-start gap-[13px] px-5 py-[18px]">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="2" className="mt-[2px] flex-none"><path d="M12 2v6M12 22v-2M5 12H2M22 12h-3" /><circle cx="12" cy="12" r="4" /></svg>
            <div className="text-[13.5px] font-medium leading-[1.6] text-soft">{narr}</div>
          </Card>

          {!auth.token && (
            <Card hover={false} className="px-5 py-[14px]">
              <div className="text-[12.5px] font-medium leading-[1.5] text-mute">Sign in to project against your seeded twin — the figures above are illustrative preview data.</div>
            </Card>
          )}

          {usingFallback && projError && (
            <Card hover={false} className="px-5 py-[14px]">
              <div className="text-[12.5px] font-medium leading-[1.5] text-[#b98a6a]">Couldn't reach the projection service ({projError}). Showing an illustrative estimate — adjust a control to retry.</div>
            </Card>
          )}
        </div>
      </div>
    </section>
  );
}
