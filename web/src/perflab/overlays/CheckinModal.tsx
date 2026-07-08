// src/perflab/overlays/CheckinModal.tsx
import { useState } from "react";
import type { InputHTMLAttributes } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/auth/useAuth";
import { getReadiness, ingestWellness, updateProfile } from "@/api/perfLabClient";
import type { ApiError, ReadinessScore } from "@/types";
import { usePerfLab } from "../store";
import { buildCheckin, readinessColor, readinessWord } from "../sim";
import {
  WELLNESS_SIGNALS,
  buildWellnessSample,
  type SignalMode,
  type WellnessSignalKey,
} from "../wellnessSignals";
import { ReadinessRing } from "../ui";
import { CloseBtn } from "./LogWorkoutModal";

const SORE: ["none" | "mild" | "moderate" | "high", string][] = [
  ["none", "None"],
  ["mild", "Mild"],
  ["moderate", "Moderate"],
  ["high", "High"],
];

const CONF_WORD: Record<string, string> = { high: "High", medium: "Medium", low: "Low" };

function Slider({ label, display, ...rest }: { label: string; display: string } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div>
      <div className="mb-[9px] flex items-center justify-between">
        <span className="font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.12em] text-mute">{label}</span>
        <span className="font-mono text-[13px] font-semibold leading-none text-ac">{display}</span>
      </div>
      <input type="range" {...rest} className="w-full cursor-pointer" style={{ accentColor: "var(--ac)" }} />
    </div>
  );
}

/** Small per-signal state toggle: mark a tracked signal "unknown today" or stop tracking it. */
function SignalControls({
  mode,
  onUnknown,
  onDontTrack,
}: {
  mode: SignalMode;
  onUnknown: () => void;
  onDontTrack: () => void;
}) {
  return (
    <div className="mt-[7px] flex items-center gap-3">
      <button
        onClick={onUnknown}
        className={cn(
          "text-[10.5px] font-semibold leading-none underline-offset-2 hover:underline",
          mode === "unknown" ? "text-ac" : "text-faint",
        )}
      >
        {mode === "unknown" ? "✓ Unknown today" : "I don't know today"}
      </button>
      <span className="text-faint/40">·</span>
      <button onClick={onDontTrack} className="text-[10.5px] font-semibold leading-none text-faint hover:underline">
        I don't track this
      </button>
    </div>
  );
}

export function CheckinModal() {
  const { state, actions } = usePerfLab();
  const auth = useAuth();
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState<ReadinessScore | null>(null);

  // Three-state, seeded from the athlete's persisted opt-outs (ADR-0053). Local so the
  // guest/sim path is untouched; signed-in edits persist to the profile.
  const [untracked, setUntracked] = useState<Set<WellnessSignalKey>>(
    () => new Set((auth.profile?.untracked_wellness_signals ?? []) as WellnessSignalKey[]),
  );
  const [modes, setModes] = useState<Record<WellnessSignalKey, SignalMode>>(() => ({
    sleep: "provided", hrv: "provided", rhr: "provided", soreness: "provided", mood: "provided", stress: "provided",
  }));

  if (!state.checkinOpen) return null;
  const ci = state.checkin;
  const sim = buildCheckin(ci);
  const signedIn = !!auth.token;

  const shown = WELLNESS_SIGNALS.filter((s) => !untracked.has(s.key));
  const hidden = WELLNESS_SIGNALS.filter((s) => untracked.has(s.key));

  const setMode = (key: WellnessSignalKey, mode: SignalMode) =>
    setModes((m) => ({ ...m, [key]: mode }));

  async function persistUntracked(next: Set<WellnessSignalKey>) {
    if (!auth.token) return;
    try {
      await updateProfile({ untracked_wellness_signals: [...next] }, auth.token);
    } catch {
      /* best-effort — local state still reflects the choice this session */
    }
  }

  function dontTrack(key: WellnessSignalKey) {
    const next = new Set(untracked).add(key);
    setUntracked(next);
    void persistUntracked(next);
  }

  function track(key: WellnessSignalKey) {
    const next = new Set(untracked);
    next.delete(key);
    setUntracked(next);
    setMode(key, "provided");
    void persistUntracked(next);
  }

  // Signed in: persist the check-in (POST /v1/wellness), honestly encoding gaps, then pull
  // the backend-owned readiness + confidence (GET /v1/readiness). Signed out: local sim only.
  async function save() {
    if (!auth.token) {
      actions.applyCheckin();
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await ingestWellness(buildWellnessSample(ci, modes, untracked), auth.token);
      const r = await getReadiness(auth.token);
      setSaved(r);
      actions.cacheReadiness(r);
      actions.applyCheckin();
    } catch (e) {
      setSaveError(
        (e as ApiError)?.message ??
          "Couldn't sync your check-in — check you're signed in and the backend is reachable.",
      );
    } finally {
      setSaving(false);
    }
  }

  const isUnknown = (k: WellnessSignalKey) => modes[k] === "unknown";
  const dim = (k: WellnessSignalKey) => (isUnknown(k) ? "opacity-40" : "");

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
          <div className="flex flex-col gap-[20px] border-r border-white/[0.06] p-6">
            {shown.map((s) => (
              <div key={s.key} className={dim(s.key)}>
                {s.key === "sleep" && (
                  <>
                    <Slider label="Sleep duration" display={`${ci.sleepH} h`} min={4} max={10} step={0.5} value={ci.sleepH} disabled={isUnknown("sleep")} onChange={(e) => actions.setCheckin({ sleepH: +e.target.value })} />
                    <div className="mt-3">
                      <Slider label="Sleep quality" display={`${ci.sleepQ}/5`} min={1} max={5} step={1} value={ci.sleepQ} disabled={isUnknown("sleep")} onChange={(e) => actions.setCheckin({ sleepQ: +e.target.value })} />
                    </div>
                  </>
                )}
                {s.key === "soreness" && (
                  <>
                    <div className="mb-[9px] font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.12em] text-mute">Soreness</div>
                    <div className="flex gap-2">
                      {SORE.map(([k, label]) => (
                        <div key={k} onClick={() => !isUnknown("soreness") && actions.setCheckin({ soreness: k })}
                          className={cn("flex-1 cursor-pointer rounded-[9px] border px-[6px] py-[10px] text-center text-[12px] font-semibold leading-none", ci.soreness === k ? "border-ac/40 bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute")}>
                          {label}
                        </div>
                      ))}
                    </div>
                  </>
                )}
                {s.key === "mood" && (
                  <Slider label="Motivation" display={`${ci.mood} / 5`} min={1} max={5} step={1} value={ci.mood} disabled={isUnknown("mood")} onChange={(e) => actions.setCheckin({ mood: +e.target.value })} />
                )}
                {s.key === "stress" && (
                  <Slider label="Stress" display={`${ci.stress} / 5`} min={1} max={5} step={1} value={ci.stress} disabled={isUnknown("stress")} onChange={(e) => actions.setCheckin({ stress: +e.target.value })} />
                )}
                {s.key === "hrv" && (
                  <Slider label="HRV (overnight)" display={`${ci.hrv} ms`} min={30} max={110} step={1} value={ci.hrv} disabled={isUnknown("hrv")} onChange={(e) => actions.setCheckin({ hrv: +e.target.value })} />
                )}
                {s.key === "rhr" && (
                  <Slider label="Resting HR" display={`${ci.rhr} bpm`} min={38} max={72} step={1} value={ci.rhr} disabled={isUnknown("rhr")} onChange={(e) => actions.setCheckin({ rhr: +e.target.value })} />
                )}
                {signedIn && (
                  <SignalControls
                    mode={modes[s.key]}
                    onUnknown={() => setMode(s.key, isUnknown(s.key) ? "provided" : "unknown")}
                    onDontTrack={() => dontTrack(s.key)}
                  />
                )}
              </div>
            ))}

            {signedIn && hidden.length > 0 && (
              <div className="mt-1 border-t border-white/[0.06] pt-[14px]">
                <div className="mb-[10px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">Not tracked — add if you have it</div>
                <div className="flex flex-wrap gap-2">
                  {hidden.map((s) => (
                    <button key={s.key} onClick={() => track(s.key)} title={s.hint}
                      className="rounded-[8px] border border-white/10 bg-panel px-3 py-[7px] text-[11.5px] font-semibold leading-none text-mute hover:border-ac/40 hover:text-ac">
                      + {s.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="flex flex-col items-center gap-[16px] bg-white/[0.015] p-6">
            <div className="self-start font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">Today's readiness</div>
            {saved && saved.score != null ? (
              <>
                <ReadinessRing value={Math.round(saved.score)} color={readinessColor(saved.score)} innerClassName="bg-surface" />
                <div className="text-[16px] font-bold leading-none" style={{ color: readinessColor(saved.score) }}>{readinessWord(saved.score)}</div>
                {saved.confidence && (
                  <div className="w-full border-t border-white/[0.06] pt-[13px]">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[#8b919c]">Confidence</span>
                      <span className="text-[12.5px] font-bold text-ink">{CONF_WORD[saved.confidence.band] ?? saved.confidence.band}</span>
                    </div>
                    {saved.confidence.recommendation_gate?.message && (
                      <p className="mt-[10px] text-[11px] font-medium leading-[1.5] text-dim">{saved.confidence.recommendation_gate.message}</p>
                    )}
                    {saved.confidence.band === "low" && (
                      <p className="mt-[8px] text-[11px] font-medium leading-[1.5] text-warn">Limited information today — add a signal or two to improve confidence.</p>
                    )}
                  </div>
                )}
              </>
            ) : (
              <>
                <ReadinessRing value={sim.readiness} color={readinessColor(sim.readiness)} innerClassName="bg-surface" />
                <div className="text-[16px] font-bold leading-none" style={{ color: readinessColor(sim.readiness) }}>{readinessWord(sim.readiness)}</div>
                <p className="text-[11px] font-medium leading-[1.5] text-faint">
                  {signedIn ? "Estimate — your real readiness is computed on save." : "Sign in to compute your backend readiness."}
                </p>
              </>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between gap-[9px] border-t border-white/[0.06] px-6 py-4">
          <span className={cn("max-w-[360px] text-[11px] font-medium leading-[1.4]", saveError ? "text-hot" : "text-dim")}>
            {saveError ??
              (signedIn
                ? "Missing a signal is fine — readiness works without it, and untracked signals never count against you."
                : "Readiness seeds today's recommended session and your twin's starting state.")}
          </span>
          <div className="flex flex-none gap-[9px]">
            <button onClick={actions.closeCheckin} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft">Close</button>
            <button onClick={save} disabled={saving} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-[18px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60">
              {saving ? "Saving…" : "Set today's readiness →"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
