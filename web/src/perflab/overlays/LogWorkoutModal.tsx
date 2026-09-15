// src/perflab/overlays/LogWorkoutModal.tsx
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/auth/useAuth";
import {
  getNextSession,
  listExercises,
  listPlannedSessions,
  logWorkout,
  simulateDose,
} from "@/api/perfLabClient";
import type { ApiError } from "@/types";
import { usePerfLab } from "../store";
import { MetricBar } from "../ui";
import { COLORS, DOSE_NAMES, doseBarColor, PRESETS, projectLogDose } from "../sim";
import { SetBuilder } from "./SetBuilder";
import { deriveModality, groupsToSets, type SetGroup } from "./setBuilderLogic";
import {
  exercisesFromStoredPrescription,
  isoLocalDate,
  pickTodaysPendingSession,
  plannedGroup,
} from "./prescriptionPrefill";
// #199: the request body is built in its own fixture-free module so the static
// reachability guard (workoutLogBoundary.test.ts) can root there. This file cannot be
// a root — it value-imports the fixture module `../sim` for its preview chrome below.
import {
  buildWorkoutLog,
  checkinToWorkoutWellness,
  missingRequiredReadings,
  type RequiredReading,
} from "./workoutLogBody";

/** How each missing reading is named to the athlete in the footer prompt. */
const READING_LABEL: Record<RequiredReading, string> = {
  duration: "duration",
  effort: "perceived effort",
  distance: "distance",
};

export function LogWorkoutModal() {
  const { state, actions } = usePerfLab();
  const auth = useAuth();
  const [doseSix, setDoseSix] = useState<number[] | null>(null);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [sets, setSets] = useState<SetGroup[]>([]);

  const { logOpen, logType, rpe, durationMin, distanceKm } = state;
  // The ONLY read of check-in state on this path, and it is null-preserving: an
  // un-entered slider stays `null` here and is omitted from the body, never defaulted.
  const wellness = checkinToWorkoutWellness(state.checkin);

  const derivedModality = sets.length ? deriveModality(sets) : null;
  // A stable key over just the fields the dose depends on, so the preview effect
  // re-runs when a set's load/reps/rpe change without chasing object identity.
  const setsKey = JSON.stringify(groupsToSets(sets));
  const { sleepQuality, lifeStressInverse } = wellness;

  // Which planned session the pre-fill came from. The body links it only once the athlete
  // records one of its exercises (buildWorkoutLog).
  const [plannedSessionId, setPlannedSessionId] = useState<number | null>(null);
  const goal = state.settings.goal;

  // On open, best-effort pre-fill from the workout the app is recommending (which one:
  // prescriptionPrefill.ts). Each exercise arrives as a TARGET the athlete confirms or
  // overwrites — never as a reading. Silent no-op when signed out or nothing is prescribed.
  useEffect(() => {
    if (!logOpen || !auth.token) return;
    const token = auth.token;
    let cancelled = false;
    (async () => {
      try {
        const today = isoLocalDate(new Date());
        const sessions = await listPlannedSessions(token, { start_date: today, end_date: today }).catch(
          () => [],
        );
        const pending = pickTodaysPendingSession(sessions, today);
        let exercises = exercisesFromStoredPrescription(pending?.prescribed_content);
        if (!exercises.length) exercises = (await getNextSession(goal, token)).exercises ?? [];
        if (!exercises.length) return;
        const matches = await Promise.all(
          exercises.map((ex) =>
            listExercises({ q: ex.name })
              .then((found) => found.find((m) => m.name === ex.name) ?? null)
              .catch(() => null),
          ),
        );
        if (cancelled) return;
        const base = Date.now();
        const groups: SetGroup[] = exercises.map((ex, i) => plannedGroup(ex, matches[i], base + i));
        // Never overwrite anything the athlete started entering while this loaded.
        setSets((current) => (current.length ? current : groups));
        setPlannedSessionId(pending?.id ?? null);
      } catch {
        // best-effort — never block the log on a prescription fetch
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [logOpen, auth.token, goal]);

  // Real D(t) preview from POST /v1/simulate-dose (debounced); falls back to the
  // sim bars while loading or if the call fails. Unauthenticated — works signed out.
  useEffect(() => {
    if (!logOpen) {
      setDoseSix(null);
      setSets([]);
      setPlannedSessionId(null);
      return;
    }
    let cancelled = false;
    const id = window.setTimeout(() => {
      // Until every required reading is entered there is no honest body to send, so
      // the preview falls back to the sample bars rather than simulating a fiction.
      const body = buildWorkoutLog(logType, rpe, durationMin, distanceKm, wellness, sets);
      if (!body) {
        if (!cancelled) setDoseSix(null);
        return;
      }
      simulateDose(body)
        .then((d) => {
          if (cancelled) return;
          const s = d.dose_six;
          setDoseSix(s ? [s.volume, s.intensity, s.density, s.impact, s.skill, s.metabolic] : null);
        })
        .catch(() => {
          if (!cancelled) setDoseSix(null);
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(id);
    };
    // setsKey is a stable serialization of `sets` — it captures every set field the
    // dose depends on without re-running on unrelated object-identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logOpen, logType, rpe, durationMin, distanceKm, sleepQuality, lifeStressInverse, setsKey]);

  if (!state.logOpen) return null;

  const { scaled, readyAfter, fatAfter, capDelta, cap, zone, readyColor } = projectLogDose(state);
  const bars = doseSix ?? scaled;

  // The draft opens entirely unknown, so this is non-empty until the athlete enters
  // real readings. Signing in is still allowed while incomplete — only logging is not.
  const missing = missingRequiredReadings(logType, rpe, durationMin, distanceKm, sets);
  const blocked = Boolean(auth.token) && missing.length > 0;
  const missingLabel = missing.map((m) => READING_LABEL[m]).join(" and ");

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
      const body = buildWorkoutLog(logType, rpe, durationMin, distanceKm, wellness, sets, plannedSessionId);
      if (!body) {
        // Unreachable while the button is disabled; kept so this path can never
        // fabricate a reading if a future caller bypasses the gate.
        setApplyError("Enter every reading above before logging this session.");
        return;
      }
      const sv = await logWorkout(body, auth.token);
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
    if (v.trim() === "") return actions.setPaceSec(null);
    const m = v.match(/(\d+):(\d+)/);
    if (m) actions.setPaceSec(+m[1] * 60 + +m[2]);
    else if (!isNaN(parseFloat(v))) actions.setPaceSec(parseFloat(v));
  };
  // Emptying a field returns it to "not entered". It must never fall to 0, which the
  // backend would record as a real reading of zero.
  const num = (set: (n: number | null) => void) => (v: string) => {
    if (v.trim() === "") return set(null);
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
              <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Duration</span><input placeholder="e.g. 42 min" onChange={(e) => num(actions.setDur)(e.target.value)} className={fieldCls} /></label>
              <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Distance</span><input placeholder="e.g. 9.0 km" onChange={(e) => num(actions.setDist)(e.target.value)} className={fieldCls} /></label>
              <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Avg pace</span><input placeholder="e.g. 4:38 /km" onChange={(e) => onPace(e.target.value)} className={fieldCls} /></label>
              <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Zone</span><div className="mt-2 w-full rounded-[11px] border border-ac/20 bg-ac/[0.06] px-[13px] py-[11px] font-mono text-[14px] font-semibold leading-[1.1] text-ac">{zone}</div></label>
            </div>
            <div>
              <div className="mb-[10px] flex items-center justify-between">
                <span className="font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">Perceived effort</span>
                <span className={cn("font-mono text-[14px] font-semibold leading-none", state.rpe === null ? "text-dim" : "text-ac")}>{state.rpe ?? "—"} <span className="text-[11px] text-dim">/ 10 RPE</span></span>
              </div>
              {/* A range input always renders somewhere, so an untouched slider would
                  look like a reported 7. It shows the midpoint but reads "—" until the
                  athlete touches it; clicking commits the displayed value, which is the
                  explicit confirmation a drag would otherwise never produce at 7. */}
              <input type="range" min={1} max={10} value={state.rpe ?? 7} onChange={(e) => actions.setRpe(+e.target.value)} onClick={() => { if (state.rpe === null) actions.setRpe(7); }} className="w-full cursor-pointer" style={{ accentColor: "var(--ac)" }} aria-label={state.rpe === null ? "Perceived effort — not set" : `Perceived effort ${state.rpe} of 10`} />
            </div>

            {/* Per-set, catalog-bound entry (ADR-0045). Optional — leaving it empty
                logs a session-level workout exactly as before. */}
            <div>
              <div className="mb-3 flex items-center justify-between">
                <span className="font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">Exercises · per set</span>
                {derivedModality && (
                  <span className="rounded-[7px] border border-ac/25 bg-ac/[0.1] px-2 py-[5px] font-mono text-[10px] font-semibold leading-none tracking-[0.08em] text-ac">
                    {derivedModality}
                  </span>
                )}
              </div>
              <SetBuilder groups={sets} onChange={setSets} />
            </div>
          </div>

          {/* preview */}
          <div className="flex flex-col gap-5 bg-white/[0.015] px-6 py-[22px]">
            <div>
              <div className="mb-[13px] font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">Projected dose · D(t)</div>
              <div className="flex flex-col gap-[10px]">
                {bars.map((v, i) => (
                  <MetricBar
                    key={DOSE_NAMES[i]}
                    label={DOSE_NAMES[i]}
                    value={v.toFixed(1)}
                    pct={Math.min(100, v * 10)}
                    color={doseBarColor(v)}
                    labelClassName="w-[62px]"
                    valueClassName="w-[26px] text-soft"
                    trackClassName="h-[6px]"
                  />
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
              (blocked
                ? `Enter ${missingLabel} to log this session.`
                : auth.token
                  ? "Applying logs the session and advances S(t) via the backend."
                  : "Sign in to log this session to your twin.")}
          </span>
          <div className="flex flex-none gap-[9px]">
            <button onClick={actions.closeLog} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft">Cancel</button>
            <button onClick={apply} disabled={applying || blocked} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-[18px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60">
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
