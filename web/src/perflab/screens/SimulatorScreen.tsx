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
import { usePerfLab, SIM_PRESETS, TRAINING_GOALS, type SimPresetName } from "../store";
import { Card, Pill, ScreenHeader, SectionLabel, Tile } from "../ui";
import { Chart, Area, Line, Marker, Meter, useVizTheme } from "../viz";
import { COLORS, readinessColor, readinessWord } from "../readinessPresentation";
import { useAuthedResource } from "../useAuthedResource";
import { assertNever, type AuthedResource } from "../resource";
import {
  placeholderProjection,
  dominantAxes,
  goalLabel,
  type ProjectionResponse,
  type ProjectionAxis,
} from "../projection";

// Console chip: solid accent + glow when selected, faint fill when not. The
// on-accent ink is mode-invariant, so it stays a literal.
const chip = (active: boolean) =>
  cn(
    "cursor-pointer border border-white/[0.07] font-semibold leading-none transition-colors",
    active
      ? "bg-ac text-[#0a0c10] shadow-[0_6px_18px_-8px_var(--ac)]"
      : "bg-white/[0.04] text-soft hover:bg-white/[0.06]",
  );
const seg = (active: boolean) => cn(chip(active), "flex-1 rounded-[9px] px-[6px] py-[10px] text-center text-[12px]");

// Console section label (mono 10px, faint) — a local override of the shared
// SectionLabel default, which other screens still render at 11px.
const LABEL = "text-[10px] text-faint";
const TILE_LABEL = "font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.12em] text-faint";

const PRESET_ORDER: SimPresetName[] = ["maintain", "build", "aggressive"];

const fmtDelta = (n: number) => `${n >= 0 ? "+" : "−"}${Math.abs(Math.round(n))}`;
const fmtPct = (n: number) => `${n >= 0 ? "+" : "−"}${Math.abs(Math.round(n * 100))}%`;
const relGain = (a: ProjectionAxis) => (a.baseline > 0 ? (a.projected - a.baseline) / a.baseline : 0);

// ─── SINGLE PROJECTION SOURCE ────────────────────────────────────────────────
// This screen always renders a complete projection, so it can never branch away
// to a notice card the way <ResourceState> surfaces do. It therefore consumes
// the canonical resource contract through the other sanctioned pattern: one
// exhaustive switch that turns the union into everything the body needs to say.
//
//   guest    → the deterministic local placeholder, labelled as preview data
//              (a guest seeing a working, clearly-labelled simulation is the
//              settled design here — it is a simulator, not a data readout)
//   loading  → the placeholder while the first real projection is in flight
//   error    → the placeholder, plus a plain note that the service was unreachable
//   success  → the real projection; a refresh keeps it on screen underneath
interface ProjectionView {
  /** The projection actually rendered: the athlete's when there is one, else the local one. */
  proj: ProjectionResponse;
  /** Signed out — every figure on screen is illustrative and must say so. */
  preview: boolean;
  /** A projection request is in flight for a signed-in athlete. */
  projecting: boolean;
  /** No usable projection at all: the failure to show beside the illustrative estimate. */
  unreachable: string | null;
}

function projectionView(
  resource: AuthedResource<ProjectionResponse>,
  placeholder: ProjectionResponse,
): ProjectionView {
  switch (resource.status) {
    case "guest":
      return { proj: placeholder, preview: true, projecting: false, unreachable: null };

    case "loading":
      return { proj: placeholder, preview: false, projecting: true, unreachable: null };

    case "error":
      return {
        proj: placeholder,
        preview: false,
        projecting: false,
        unreachable: resource.error.message,
      };

    case "success":
      // A failed refresh is NOT `unreachable` — the previous projection is still
      // the athlete's own, so it stays on screen rather than being disowned.
      return {
        proj: resource.data,
        preview: false,
        projecting: resource.refresh.status === "loading",
        unreachable: null,
      };

    default:
      return assertNever(resource);
  }
}

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

  // Signed in → project against the seeded twin via the real endpoint. Guest /
  // signed out → the deterministic local placeholder. See `projectionView` above
  // for how each state of that one resource is spoken to the athlete.
  const placeholder: ProjectionResponse = placeholderProjection({
    goal: sim.goal,
    weeks: sim.weeks,
    weekly_volume: sim.volume,
    intensity: sim.intensity,
    recovery: sim.recovery,
  });
  const projection = useAuthedResource<ProjectionResponse>(
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
  // A control change refetches over an existing projection, which stays `success`
  // — so the athlete's last real projection holds the screen through the refetch,
  // and the local one appears only before any result or when there is none.
  const view = projectionView(projection, placeholder);
  const proj = view.proj;

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
        title="Simulator"
        badge={<Pill>what-if · X(t) projection</Pill>}
        subtitle="Run your twin forward against a goal — all eight capacity axes, readiness and fatigue, measured against simply maintaining."
      >
        {view.preview && <Pill className="border-white/15 bg-white/[0.06] text-mute">preview data</Pill>}
        {view.projecting && (
          <Pill className="border-ac/25 bg-ac/[0.08] text-ac">projecting…</Pill>
        )}
        {/* Check-in writes wellness: signed-in athletes only, as on Twin and Overview. */}
        {auth.token != null && (
          <button onClick={actions.openCheckin} className="rounded-[9px] border border-white/[0.07] bg-white/[0.04] px-[14px] py-[9px] text-[12.5px] font-semibold leading-none text-soft">
            Check in
          </button>
        )}
        <button onClick={actions.openLog} className="rounded-[9px] bg-ac px-[15px] py-[9px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Log workout</button>
      </ScreenHeader>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[360px_1fr]">
        {/* ── Controls ── */}
        <Card hover={false} className="flex flex-col gap-5 self-start p-[22px]">
          <div>
            <SectionLabel className={cn(LABEL, "mb-[11px]")}>Quick scenarios</SectionLabel>
            <div className="flex gap-2">
              {PRESET_ORDER.map((p) => {
                // Active only while all three values it sets still match.
                const cfg = SIM_PRESETS[p];
                const active = sim.volume === cfg.volume && sim.intensity === cfg.intensity && sim.recovery === cfg.recovery;
                return (
                  <button key={p} onClick={() => actions.simPreset(p)} aria-pressed={active} className={cn(chip(active), "flex-1 rounded-[10px] px-[6px] py-[11px] text-center text-[12px]")}>{p}</button>
                );
              })}
            </div>
          </div>
          <div className="h-px bg-white/[0.07]" />
          <div>
            <SectionLabel className={cn(LABEL, "mb-[11px]")}>Goal</SectionLabel>
            {/* Every training goal the backend accepts is offered: the mock's six
                are its sample subset, and a profile goal like Powerlifting must
                stay selectable. */}
            <div role="group" aria-label="Goal" className="flex flex-wrap gap-[7px]">
              {TRAINING_GOALS.map((g) => (
                <button key={g.value} onClick={() => actions.setSim({ goal: g.value })} aria-pressed={sim.goal === g.value} className={cn(chip(sim.goal === g.value), "rounded-[8px] px-[11px] py-[9px] text-[12px]")}>{g.label}</button>
              ))}
            </div>
            <div className="mt-[7px] font-mono text-[10px] leading-none text-dim">shapes which axes grow</div>
          </div>
          <div>
            <div className="mb-3 flex items-center justify-between">
              <SectionLabel className={LABEL}>Weekly volume</SectionLabel>
              <span className="font-mono text-[13px] font-semibold leading-none text-ac">{sim.volume}</span>
            </div>
            <input type="range" min={30} max={90} step={2} value={sim.volume} onChange={(e) => actions.setSim({ volume: +e.target.value })} className="w-full cursor-pointer" style={{ accentColor: "var(--ac)" }} />
            <div className="mt-[6px] flex justify-between font-mono text-[10px] leading-none text-dim"><span>30</span><span>90</span></div>
          </div>
          <div>
            <SectionLabel className={cn(LABEL, "mb-[11px]")}>Training intensity</SectionLabel>
            <div className="flex gap-2">
              {(["easy", "balanced", "hard"] as const).map((v) => (
                <button key={v} onClick={() => actions.setSim({ intensity: v })} aria-pressed={sim.intensity === v} className={seg(sim.intensity === v)}>{v}</button>
              ))}
            </div>
          </div>
          <div>
            <SectionLabel className={cn(LABEL, "mb-[11px]")}>Recovery emphasis</SectionLabel>
            <div className="flex gap-2">
              {(["high", "standard", "minimal"] as const).map((v) => (
                <button key={v} onClick={() => actions.setSim({ recovery: v })} aria-pressed={sim.recovery === v} className={seg(sim.recovery === v)}>{v}</button>
              ))}
            </div>
          </div>
          <div>
            <SectionLabel className={cn(LABEL, "mb-[11px]")}>Horizon</SectionLabel>
            <div className="flex gap-2">
              {[4, 8, 12, 16].map((wk) => (
                <button key={wk} onClick={() => actions.setSim({ weeks: wk })} aria-pressed={sim.weeks === wk} className={seg(sim.weeks === wk)}>{wk} wk</button>
              ))}
            </div>
          </div>
        </Card>

        {/* ── Output ── */}
        <div className="flex flex-col gap-4">
          {/* Stat tiles */}
          <div className="grid grid-cols-2 gap-[14px] lg:grid-cols-4">
            <Tile className="p-4">
              <div className={TILE_LABEL}>End readiness</div>
              <div className="mt-[11px] flex items-baseline gap-[7px]">
                <span className="font-mono text-[26px] font-semibold leading-none" style={{ color: rColor }}>{endReady}</span>
                <span className="text-[12px] font-semibold leading-none" style={{ color: rColor }}>{readinessWord(endReady)}</span>
              </div>
              <div className="mt-2 text-[11px] font-medium leading-none text-faint">at week {weeks}</div>
            </Tile>
            <Tile className="p-4">
              <div className={TILE_LABEL}>Peak fatigue</div>
              <div className="mt-[11px] font-mono text-[26px] font-semibold leading-none" style={{ color: peakColor }}>{proj.peak_fatigue}</div>
              <Meter variant="bare" pct={proj.peak_fatigue} color={peakColor} trackClassName="h-[5px]" className="mt-3" />
            </Tile>
            <Tile className="p-4">
              <div className={TILE_LABEL}>Top gain</div>
              <div className="mt-[11px] font-mono text-[19px] font-semibold leading-none text-teal">{topAxis.label}</div>
              <div className="mt-2 text-[11px] font-semibold leading-none text-good">{fmtPct(relGain(topAxis))} vs maintain</div>
            </Tile>
            <Tile className="p-4">
              <div className={TILE_LABEL}>Plan uplift</div>
              <div className="mt-[11px] font-mono text-[26px] font-semibold leading-none text-ink">{fmtPct(avgUplift)}</div>
              <div className="mt-2 text-[11px] font-medium leading-none text-faint">avg across 8 axes</div>
            </Tile>
          </div>

          {/* 8-axis start → projected */}
          <Card className="p-5">
            <div className="mb-[18px] flex items-center justify-between">
              <SectionLabel className={LABEL}>Capacity projection · X(t)</SectionLabel>
              <div className="flex items-center gap-4">
                <span className="flex items-center gap-[7px] text-[11px] font-medium leading-none text-soft"><span className="h-[6px] w-4 rounded-[2px] bg-ac" />projected</span>
                <span className="flex items-center gap-[7px] text-[11px] font-medium leading-none text-mute"><span className="h-[10px] w-[2px] bg-faint" />maintain</span>
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
                    <div className="relative mb-[7px] mt-[11px] h-[6px] overflow-hidden rounded-full bg-white/[0.08]">
                      <div className="h-full rounded-full" style={{ width: `${fillPct}%`, background: "linear-gradient(90deg,var(--ac),#a7e36e)" }} />
                      <div className="absolute top-[-2px] h-[10px] w-[2px] rounded-full bg-faint" style={{ left: `calc(${basePct}% - 1px)` }} />
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
              <SectionLabel className={LABEL}>Trajectory · {selAxis.label}</SectionLabel>
              <div className="flex items-center gap-4">
                <span className="flex items-center gap-[7px] text-[11px] font-medium leading-none text-soft"><span className="h-[3px] w-4 rounded-[2px] bg-ac" />This plan</span>
                <span className="flex items-center gap-[7px] text-[11px] font-medium leading-none text-mute"><span className="w-4 border-t-2 border-dashed border-faint" />Maintain</span>
              </div>
            </div>
            <div className="mb-[14px] flex flex-wrap gap-[6px]">
              {axes.map((a) => (
                <button key={a.key} onClick={() => setSelKey(a.key)} aria-pressed={a.key === activeKey} className={cn(chip(a.key === activeKey), "rounded-[7px] px-[9px] py-[7px] text-[11px]")}>{a.label}</button>
              ))}
            </div>
            <Chart
              width={520}
              height={180}
              padding={{ top: 14, right: 6, bottom: 14, left: 6 }}
              xDomain={[0, weeks]}
              yDomain={[tLo - tPad, tHi + tPad]}
              ariaLabel={`${selAxis.label} trajectory versus maintaining`}
              className="h-[190px] w-full"
            >
              <Line data={trajData(selAxis.baseline_series)} color={colors.text.faint} width={1.6} dashed label="Maintain" />
              <Area data={trajData(selAxis.series)} color={accent} fillOpacity={0.14} label="This plan" />
              <Marker x={weeks} y={selAxis.projected} color={accent} />
            </Chart>
            <div className="mt-1 flex justify-between font-mono text-[10px] leading-none text-dim"><span>now · {Math.round(selAxis.start)}</span><span>{weeks} wk · {Math.round(selAxis.projected)}</span></div>
          </Card>

          {/* Readiness curve */}
          <Card className="p-5">
            <div className="mb-[10px] flex items-center justify-between">
              <SectionLabel className={LABEL}>Readiness under this plan</SectionLabel>
              <span className="font-mono text-[10px] leading-none text-dim">0–100 scale</span>
            </div>
            <Chart
              width={520}
              height={150}
              padding={{ top: 10, right: 6, bottom: 10, left: 6 }}
              xDomain={[0, weeks]}
              yDomain={[0, 100]}
              ariaLabel="Readiness under this plan"
              className="h-[150px] w-full"
            >
              <Area data={proj.readiness_series.map((v, i) => [i, v] as [number, number])} color={COLORS.teal} fillOpacity={0.14} label="Readiness" />
              <Marker x={weeks} y={endReady} color={COLORS.teal} />
            </Chart>
            <div className="mt-1 flex justify-between font-mono text-[10px] leading-none text-dim"><span>now</span><span>{weeks} wk · {endReady}</span></div>
          </Card>

          {/* Narrative */}
          <Card className="flex items-start gap-[13px] px-5 py-[18px]">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="2" className="mt-[2px] flex-none"><path d="M12 2v6M12 22v-2M5 12H2M22 12h-3" /><circle cx="12" cy="12" r="4" /></svg>
            <div className="text-[13.5px] font-medium leading-[1.6] text-soft">{narr}</div>
          </Card>

          {view.preview && (
            <Card hover={false} className="px-5 py-[14px]">
              <div className="text-[12.5px] font-medium leading-[1.5] text-mute">Sign in to project against your seeded twin — the figures above are illustrative preview data.</div>
            </Card>
          )}

          {view.unreachable && (
            <Card hover={false} className="px-5 py-[14px]">
              <div className="text-[12.5px] font-medium leading-[1.5] text-warn">Couldn't reach the projection service ({view.unreachable}). Showing an illustrative estimate — adjust a control to retry.</div>
            </Card>
          )}
        </div>
      </div>
    </section>
  );
}
