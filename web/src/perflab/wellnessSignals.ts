// src/perflab/wellnessSignals.ts
//
// Frontend mirror of the backend wellness signal registry (ADR-0053). Drives the
// three-state morning check-in: each logical signal is provided / unknown-today /
// untracked. Untracked signals (the athlete's explicit opt-out, persisted on the
// profile) are hidden; "unknown today" sends `null` (an honest gap, never imputed).
import type { CheckinState } from "./sim";
import type { WellnessSampleIn } from "@/types";

export type WellnessSignalKey = "sleep" | "hrv" | "rhr" | "soreness" | "mood" | "stress";
export type SignalMode = "provided" | "unknown"; // untracked is tracked separately (profile)
export type SignalCategory = "wellness_readiness" | "biometric_recovery";

export interface WellnessSignalDef {
  key: WellnessSignalKey;
  label: string;
  category: SignalCategory;
  hint: string;
}

// Order matches the backend registry; sleep + the subjective set first, devices last.
export const WELLNESS_SIGNALS: WellnessSignalDef[] = [
  { key: "sleep", label: "Sleep", category: "wellness_readiness", hint: "Duration and quality." },
  { key: "soreness", label: "Soreness", category: "wellness_readiness", hint: "How beat-up you feel." },
  { key: "mood", label: "Motivation", category: "wellness_readiness", hint: "Drive to train today." },
  { key: "stress", label: "Stress", category: "wellness_readiness", hint: "Life/training stress load." },
  { key: "hrv", label: "HRV", category: "biometric_recovery", hint: "Overnight heart-rate variability (device)." },
  { key: "rhr", label: "Resting HR", category: "biometric_recovery", hint: "Overnight resting heart rate (device)." },
];

const SORE_TO_0_10: Record<string, number> = { none: 0, mild: 3, moderate: 6, high: 9 };

/** The WellnessSample metric value for a provided signal, from the check-in form. */
function metricValue(sig: WellnessSignalKey, c: CheckinState): Partial<WellnessSampleIn> {
  switch (sig) {
    case "sleep":
      return { sleep_hours: c.sleepH, sleep_quality: c.sleepQ * 20 }; // 1–5 → 0–100
    case "hrv":
      return { hrv_ms: c.hrv };
    case "rhr":
      return { resting_hr: c.rhr };
    case "soreness":
      return { soreness: SORE_TO_0_10[c.soreness] ?? 0 };
    case "mood":
      return { mood: c.mood * 2 }; // 1–5 → 0–10
    case "stress":
      return { stress: c.stress * 2 }; // 1–5 → 0–10, higher = worse
  }
}

/**
 * Build the POST /v1/wellness payload honestly:
 * - provided signals carry their value;
 * - unknown-today signals are omitted (→ null server-side: a gap, never imputed);
 * - untracked signals are omitted entirely (not expected).
 */
export function buildWellnessSample(
  c: CheckinState,
  modes: Record<WellnessSignalKey, SignalMode>,
  untracked: ReadonlySet<WellnessSignalKey>,
): WellnessSampleIn {
  const out: Partial<WellnessSampleIn> = {
    date: new Date().toISOString().slice(0, 10),
    source: "manual",
  };
  for (const { key } of WELLNESS_SIGNALS) {
    if (untracked.has(key) || modes[key] === "unknown") continue;
    Object.assign(out, metricValue(key, c));
  }
  return out as WellnessSampleIn;
}
