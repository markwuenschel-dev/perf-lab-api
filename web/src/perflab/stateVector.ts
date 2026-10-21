// Shared, pure state-vector reductions — the single home for the small bits of
// domain arithmetic that turn a UnifiedStateVector / StateHistorySnapshotRead
// into display numbers. Previously copied module-local across Twin, Overview and
// History ("Copied module-local from OverviewScreen" — TwinScreen). No React,
// chart, scale, or routing behavior lives here so these are unit-testable
// through their interface.
import type { CapacityState, UnifiedStateVector } from "@/types";

/** Mean of the six decomposed fatigue axes (0–100), or null when the vector is missing
 *  or any axis is non-finite. INT-05 C4: never the legacy fatigue scalars — they are a
 *  lossy four-channel mirror, so averaging them is a different number, not a fallback. */
export function meanFatigue(sv: UnifiedStateVector): number | null {
  const f = sv.fatigue_f;
  if (!f) return null;
  const vals = [f.cns, f.muscular, f.metabolic, f.structural, f.tendon, f.grip];
  if (!vals.every((v) => typeof v === "number" && Number.isFinite(v))) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

/** A fatigue-derived *display* proxy ~ (1 − mean fatigue), scaled 0–100.
 *
 *  NOT canonical readiness. Canonical readiness is backend-owned (readiness_service,
 *  PDR-0005 / ADR-0026) and reaches the client only through `getReadiness`; it mixes
 *  acute wellness the client does not have. This proxy exists solely to draw a trend
 *  line over per-snapshot state history, where no backend readiness score is carried.
 *  It must never feed decision logic — display only. (Renamed from the ambiguous
 *  `stateReadinessProxy` so it cannot be mistaken for the canonical number.) */
export function fatigueDisplayProxy(sv: UnifiedStateVector): number | null {
  const m = meanFatigue(sv);
  return m == null ? null : Math.round(Math.max(0, Math.min(100, 100 - m)));
}

/** Highest-loaded tissue region + its value, from a state vector. */
export function peakTissue(sv: UnifiedStateVector): { region: string; value: number } | null {
  const t = sv.tissue_t;
  if (!t) return null;
  const entries = Object.entries(t) as [string, number][];
  const [region, value] = entries.sort((a, b) => b[1] - a[1])[0];
  return { region: region.charAt(0).toUpperCase() + region.slice(1), value: Math.round(value) };
}

/** Compact "Xh ago" for the sync chip. */
export function relativeTime(iso: string): string {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// --- Typed, runtime-honest capacity reads --------------------------------------
// `keyof CapacityState` protects source from misspelled/renamed properties; the
// runtime check protects against malformed payloads and stale generated types.
// Missing or non-finite capacity data becomes explicit unavailability — never 0.

export type CapacityKey = keyof CapacityState;

export type CapacityRead =
  | { availability: "available"; value: number }
  | { availability: "unavailable"; reason: "missing_axis" | "non_finite_value" };

export function readCapacity(state: CapacityState | null | undefined, key: CapacityKey): CapacityRead {
  if (!state) return { availability: "unavailable", reason: "missing_axis" };
  const v = state[key];
  if (v == null) return { availability: "unavailable", reason: "missing_axis" };
  if (!Number.isFinite(v)) return { availability: "unavailable", reason: "non_finite_value" };
  return { availability: "available", value: v };
}

/** Capacity value for a state vector: the decomposed axis when present and finite, else
 *  null. Never returns 0 for missing data — a nullable result the caller must render
 *  honestly (not `?? 0`). INT-05 C4: no legacy-mirror fallback. The strength "mirror"
 *  (`c_nm_force = 21 × max_strength + 400`) is on a different scale from the axis, so the
 *  old fallback displayed a wrong number rather than a missing one. */
export function snapshotCapacity(sv: UnifiedStateVector, key: CapacityKey): number | null {
  const read = readCapacity(sv.capacity_x, key);
  return read.availability === "available" ? read.value : null;
}

/** Aerobic capacity for a state vector — the decomposed axis, or null when it is missing or
 *  non-finite (never the legacy `c_met_aerobic` mirror). */
export function aerobicValue(sv: UnifiedStateVector): number | null {
  return snapshotCapacity(sv, "aerobic");
}
