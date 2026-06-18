// src/perflab/overlays/LogWorkoutModal.tsx
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/auth/useAuth";
import { logWorkout, simulateDose } from "@/api/perfLabClient";
import type { ApiError, Modality, WorkoutLog } from "@/types";
import { usePerfLab } from "../store";
import { COLORS, DOSE_NAMES, doseBarColor, PRESETS, projectLogDose } from "../sim";

/** Build a backend WorkoutLog from the modal's form state. */
function buildWorkoutLog(
  logType: string,
  rpe: number,
  durationMin: number,
  distanceKm: number,
  sleepQ: number,
  mood: number,
): WorkoutLog {
  const modality: Modality = logType === "strength" ? "Strength" : "Running";
  return {
    timestamp: new Date().toISOString(),
    modality,
    duration_minutes: durationMin,
    session_rpe: rpe,
    // Required by the backend; sourced from the morning check-in (1–5 scales).
    sleep_quality: sleepQ,
    life_stress_inverse: mood,
    ...(modality === "Running" ? { distance_meters: Math.round(distanceKm * 1000) } : {}),
  };
}

export function LogWorkoutModal() {
  const { state, actions } = usePerfLab();
  const auth = useAuth();
  const [doseSix, setDoseSix] = useState<number[] | null>(null);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);

  const { logOpen, logType, rpe, durationMin, distanceKm } = state;
  const { sleepQ, mood } = state.checkin;

  // Real D(t) preview from POST /v1/simulate-dose (debounced); falls back to the
  // sim bars while loading or if the call fails. Unauthenticated — works signed out.
  useEffect(() => {
    if (!logOpen) {
      setDoseSix(null);
      return;
    }
    let cancelled = false;
    const id = window.setTimeout(() => {
      simulateDose(buildWorkoutLog(logType, rpe, durationMin, distanceKm, sleepQ, mood))
        .then((d) => {
          if (cancelled) return;
          const s = d.dose_six;
          setDoseSix([s.volume, s.intensity, s.density, s.impact, s.skill, s.metabolic]);
        })
        .catch(() => {
          if (!cancelled) setDoseSix(null);
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(id);
    };
  }, [logOpen, logType, rpe, durationMin, distanceKm, sleepQ, mood]);

  if (!state.logOpen) return null;

  const { scaled, readyAfter, fatAfter, capDelta, cap, zone, readyColor } = projectLogDose(state);
  const bars = doseSix ?? scaled;

  // Apply → POST /v1/log-workout (auth required), cache the returned state.
  async function apply() {
    if (!auth.token) {
      actions.closeLog();
      actions.openAuth();
      return;
    }
    setApplying(true);
    setApplyError(null);
    try {
      const sv = await logWorkout(
        buildWorkoutLog(logType, rpe, durationMin, distanceKm, sleepQ, mood),
        auth.token,
      );
      actions.cacheTwinState(sv);
      actions.applyLog();
    } catch (e) {
      setApplyError(
        (e as ApiError)?.message ??
          "Couldn't log the workout — check you're signed in and the backend is reachable.",
      );
    } finally {
      setApplying(false);
    }
  }

  const onPace = (v: string) => {
    const m = v.match(/(\d+):(\d+)/);
    if (m) actions.setPaceSec(+m[1] * 60 + +m[2]);
    else if (!isNaN(parseFloat(v))) actions.setPaceSec(parseFloat(v));
  };
  const num = (set: (n: number) => void) => (v: string) => {
    const n = parseFloat(v);
    if (!isNaN(n)) set(n);
  };

  const fieldCls = "mt-2 w-full rounded-[11px] border border-white/10 bg-panel px-[13px] py-[11px] font-mono text-[14px] text-ink";

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-8 backdrop-blur-[4px]" style={{ background: "rgba(4,5,8,.68)" }}>
      <div className="max-h-[92vh] w-[780px] max-w-full overflow-auto rounded-[18px] border border-white/[0.09] bg-surface shadow-[0_50px_110px_-30px_rgba(0,0,0,.75)]">
        <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-5">
          <div className="flex items-center gap-[10px]">
            <h2 className="m-0 text-[18px] font-bold leading-none tracking-[-0.01em] text-ink">Log workout</h2>
            <span className="rounded-[7px] border border-mint/25 bg-mint/[0.12] px-2 py-[5px] font-mono text-[10px] font-semibold leading-none tracking-[0.1em] text-[#9ad6c8]">simulate-dose</span>
          </div>
          <CloseBtn onClick={actions.closeLog} />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-[1fr_312px]">
          {/* form */}
          <div className="flex flex-col gap-[22px] border-r border-white/[0.06] px-6 py-[22px]">
            <div>
              <div className="mb-3 font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">Session type</div>
              <div className="flex flex-wrap gap-2">
                {Object.keys(PRESETS).map((k) => {
                  const active = k === state.logType;
                  return (
                    <button
                      key={k}
                      onClick={() => actions.setLogType(k)}
                      className={cn("rounded-[9px] border px-[13px] py-[9px] text-[12px] font-semibold leading-none", active ? "border-ac/[0.45] bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute")}
                    >
                      {PRESETS[k].label}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-[14px]">
              <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Duration</span><input defaultValue="42 min" onChange={(e) => num(actions.setDur)(e.target.value)} className={fieldCls} /></label>
              <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Distance</span><input defaultValue="9.0 km" onChange={(e) => num(actions.setDist)(e.target.value)} className={fieldCls} /></label>
              <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Avg pace</span><input defaultValue="4:38 /km" onChange={(e) => onPace(e.target.value)} className={fieldCls} /></label>
              <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Zone</span><div className="mt-2 w-full rounded-[11px] border border-ac/20 bg-ac/[0.06] px-[13px] py-[11px] font-mono text-[14px] font-semibold leading-[1.1] text-ac">{zone}</div></label>
            </div>
            <div>
              <div className="mb-[10px] flex items-center justify-between">
                <span className="font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">Perceived effort</span>
                <span className="font-mono text-[14px] font-semibold leading-none text-ac">{state.rpe} <span className="text-[11px] text-dim">/ 10 RPE</span></span>
              </div>
              <input type="range" min={1} max={10} value={state.rpe} onChange={(e) => actions.setRpe(+e.target.value)} className="w-full cursor-pointer" style={{ accentColor: "var(--ac)" }} />
            </div>
          </div>

          {/* preview */}
          <div className="flex flex-col gap-5 bg-white/[0.015] px-6 py-[22px]">
            <div>
              <div className="mb-[13px] font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">Projected dose · D(t)</div>
              <div className="flex flex-col gap-[10px]">
                {bars.map((v, i) => (
                  <div key={DOSE_NAMES[i]} className="flex items-center gap-[9px]">
                    <span className="w-[62px] flex-none text-[11px] font-medium leading-none text-mute">{DOSE_NAMES[i]}</span>
                    <div className="h-[6px] flex-1 overflow-hidden rounded-full bg-white/[0.07]"><div className="h-full rounded-full" style={{ width: `${Math.min(100, v * 10)}%`, background: doseBarColor(v) }} /></div>
                    <span className="w-[26px] text-right font-mono text-[11px] font-semibold leading-none text-soft">{v.toFixed(1)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="border-t border-white/[0.06] pt-[18px]">
              <div className="mb-[13px] font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">Resulting S(t) shift</div>
              <div className="flex flex-col gap-3">
                <ShiftRow label="Readiness" from="64" to={`${readyAfter}`} toColor={readyColor} />
                <ShiftRow label="Mean fatigue" from="33" to={`${fatAfter}`} toColor={COLORS.hot} />
                <div className="flex items-center justify-between">
                  <span className="text-[12px] font-medium leading-none text-mute">{cap}</span>
                  <div className="flex items-center gap-[7px]"><span className="text-[12px] font-medium leading-none text-dim">drive</span><span className="font-mono text-[15px] font-semibold leading-none text-teal">{capDelta}</span></div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between gap-[9px] border-t border-white/[0.06] px-6 py-4">
          <span className={cn("max-w-[320px] text-[11px] font-medium leading-[1.4]", applyError ? "text-hot" : "text-dim")}>
            {applyError ??
              (auth.token
                ? "Applying logs the session and advances S(t) via the backend."
                : "Sign in to log this session to your twin.")}
          </span>
          <div className="flex flex-none gap-[9px]">
            <button onClick={actions.closeLog} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft">Cancel</button>
            <button onClick={apply} disabled={applying} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-[18px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60">
              {applying ? "Applying…" : auth.token ? "Apply to twin →" : "Sign in to apply →"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function CloseBtn({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick} className="h-8 w-8 rounded-[9px] border border-white/10 bg-white/[0.03] text-[14px] leading-none text-mute">✕</button>
  );
}

function ShiftRow({ label, from, to, toColor }: { label: string; from: string; to: string; toColor: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[12px] font-medium leading-none text-mute">{label}</span>
      <div className="flex items-center gap-[7px]">
        <span className="font-mono text-[15px] font-semibold leading-none text-soft">{from}</span>
        <span className="text-[12px] font-medium leading-none text-dim">→</span>
        <span className="font-mono text-[15px] font-semibold leading-none" style={{ color: toColor }}>{to}</span>
      </div>
    </div>
  );
}
