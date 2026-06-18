// src/perflab/sim.ts
//
// Pure simulation layer ported 1:1 from the "Perf Lab" design prototype
// (the `text/x-dc` DCLogic script). Everything here is deterministic and
// client-side — there is no backend call. `DAYS` is a fixed 22-day twin
// history anchored to a "today" of 2026-06-17, exactly as in the mockup.

export const COLORS = {
  good: "#5fd08a",
  warn: "#f5c451",
  hot: "#ff8a5c",
  info: "#86b8ff",
  teal: "#7bd6c0",
  mint: "#45d6c4",
  lime: "#c6f135",
  ink: "#eef0f3",
  soft: "#cfd4dd",
  mute: "#9aa0ab",
  faint: "#646b78",
  dim: "#565d69",
} as const;

export interface TwinDay {
  F: Record<string, number>;
  T: Record<string, number>;
  C: Record<string, number>;
  readiness: number;
  vo2: number;
  profile: number;
  habit: number;
  signal: number;
  date: Date;
}

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

export const FATIGUE_ORDER = ["CNS", "Muscular", "Metabolic", "Structural", "Tendon", "Grip"];
export const TISSUE_ORDER = ["Knee", "Lumbar", "Hip", "Ankle", "Shoulder", "Elbow", "Wrist", "Finger"];

function buildDays(): TwinDay[] {
  const N = 22;
  const canonF: Record<string, number> = { CNS: 35, Muscular: 50, Metabolic: 40, Structural: 30, Tendon: 25, Grip: 20 };
  const canonT: Record<string, number> = { Knee: 40, Lumbar: 35, Hip: 30, Ankle: 25, Shoulder: 15, Elbow: 12, Wrist: 10, Finger: 8 };
  const canonC: Record<string, number> = { aerobic: 320, glyco: 65, strength: 118, power: 58, workcap: 62 };
  const fl = [0.55, 0.6, 0.5, 0.42, 0.35, 0.3, 0.28, 0.4, 0.55, 0.62, 0.5, 0.7, 0.85, 0.95, 0.78, 0.6, 0.5, 0.45, 0.52, 0.6, 0.55, 0.52];
  const tl = [0.5, 0.55, 0.6, 0.5, 0.42, 0.38, 0.35, 0.4, 0.5, 0.58, 0.55, 0.62, 0.8, 0.92, 0.85, 0.7, 0.6, 0.55, 0.6, 0.68, 0.62, 0.6];
  const today = new Date(2026, 5, 17);
  const days: TwinDay[] = [];
  for (let i = 0; i < N; i++) {
    const ff = fl[i] / fl[N - 1];
    const tt = tl[i] / tl[N - 1];
    const gp = 0.88 + 0.12 * (i / (N - 1));
    const F: Record<string, number> = {};
    Object.keys(canonF).forEach((k) => (F[k] = clamp(Math.round(canonF[k] * ff), 0, 100)));
    const T: Record<string, number> = {};
    Object.keys(canonT).forEach((k) => (T[k] = clamp(Math.round(canonT[k] * tt), 0, 100)));
    const C: Record<string, number> = {};
    Object.keys(canonC).forEach((k) => (C[k] = Math.round(canonC[k] * gp)));
    const meanF = Object.values(F).reduce((a, b) => a + b, 0) / 6;
    const maxT = Math.max(...Object.values(T));
    const readiness = clamp(Math.round(100 - 0.55 * meanF - 0.45 * maxT), 0, 100);
    const vo2 = Math.round((54 + 4.4 * (i / (N - 1))) * 10) / 10;
    const profile = Math.round((-4.8 - 2.4 * (i / (N - 1))) * 10) / 10;
    const habit = Math.round(30 + 15 * (i / (N - 1)));
    const signal = Math.round((1.0 + 1.5 * (i / (N - 1))) * 10) / 10;
    const d = new Date(today);
    d.setDate(d.getDate() - (N - 1 - i));
    days.push({ F, T, C, readiness, vo2, profile, habit, signal, date: d });
  }
  const last = days[N - 1];
  last.F = { ...canonF };
  last.T = { ...canonT };
  last.C = { ...canonC };
  last.readiness = 64;
  last.vo2 = 58.4;
  last.profile = -7.2;
  last.habit = 45;
  last.signal = 2.5;
  return days;
}

/** Fixed 22-day twin history (deterministic; today = index N-1). */
export const DAYS: TwinDay[] = buildDays();
export const DAY_COUNT = DAYS.length;

// ---- Twin Simulator: forward projection ----
export interface SimParams {
  volume: number;
  intensity: "easy" | "balanced" | "hard";
  weeks: number;
  recovery: "high" | "standard" | "minimal";
}

export interface Projection {
  vo2: number[];
  fat: number[];
  ready: number[];
  risk: number;
  vo2Final: number;
  peakFat: number;
  tenKMin: number;
}

export function buildProjection(p: SimParams): Projection {
  const baseVo2 = 58.4;
  const baseFat = 33;
  const baseVol = 48;
  const intF = p.intensity === "easy" ? 0.9 : p.intensity === "hard" ? 1.18 : 1.0;
  const recF = p.recovery === "minimal" ? 1.3 : p.recovery === "high" ? 0.76 : 1.0;
  const potential = 55 + p.volume * 0.135 + (intF - 1) * 16;
  const gain = 0.2 * (0.6 + 0.4 / recF);
  const weeklyStress = (p.volume / baseVol) * intF;
  const steadyFat = Math.min(95, baseFat * weeklyStress * recF);
  const vo2 = [baseVo2];
  const fat = [baseFat];
  const ready = [Math.round(100 - baseFat * 0.95)];
  for (let w = 1; w <= p.weeks; w++) {
    const v = vo2[w - 1] + gain * (potential - vo2[w - 1]);
    const f = fat[w - 1] + 0.42 * (steadyFat - fat[w - 1]);
    vo2.push(v);
    fat.push(f);
    ready.push(Math.round(Math.max(20, Math.min(100, 100 - f * 0.95 - Math.max(0, weeklyStress - 1) * 8))));
  }
  const ramp = (p.volume - baseVol) / baseVol;
  const risk = Math.round(Math.max(4, Math.min(96, 12 + ramp * 52 + (intF - 1) * 95 + (recF - 1) * 45)));
  const vo2Final = vo2[vo2.length - 1];
  const tenKMin = 41.5 - (vo2Final - 55) * 0.95;
  return { vo2, fat, ready, risk, vo2Final, peakFat: Math.round(Math.max(...fat)), tenKMin };
}

// ---- Morning check-in → readiness ----
export interface CheckinState {
  hrv: number;
  sleepH: number;
  sleepQ: number;
  rhr: number;
  soreness: "none" | "mild" | "moderate" | "high";
  mood: number;
  done: boolean;
}

export interface CheckinDriver {
  n: string;
  v: string;
  delta: string;
  color: string;
}

export function buildCheckin(c: CheckinState): { readiness: number; drivers: CheckinDriver[] } {
  const soreMap: Record<string, number> = { none: 8, mild: 0, moderate: -10, high: -22 };
  const parts = [
    { n: "HRV", v: c.hrv + " ms", x: (c.hrv - 62) * 0.6 },
    { n: "Sleep", v: c.sleepH + " h · quality " + c.sleepQ + "/5", x: (c.sleepH - 7) * 4 + (c.sleepQ - 3) * 3 },
    { n: "Resting HR", v: c.rhr + " bpm", x: (58 - c.rhr) * 0.9 },
    { n: "Soreness", v: c.soreness.charAt(0).toUpperCase() + c.soreness.slice(1), x: soreMap[c.soreness] || 0 },
    { n: "Motivation", v: c.mood + " / 5", x: (c.mood - 3) * 2 },
  ];
  let r = 50;
  parts.forEach((p) => (r += p.x));
  r = Math.round(Math.max(15, Math.min(98, r)));
  const drivers = parts.map((p) => {
    const d = Math.round(p.x);
    return { n: p.n, v: p.v, delta: (d >= 0 ? "+" : "") + d, color: d > 0 ? COLORS.good : d < 0 ? COLORS.hot : "#7c818c" };
  });
  return { readiness: r, drivers };
}

// ---- Color / word helpers ----
export const readinessColor = (r: number) =>
  r >= 75 ? COLORS.good : r >= 55 ? COLORS.lime : r >= 40 ? COLORS.warn : COLORS.hot;
export const readinessWord = (r: number) => (r >= 75 ? "Fresh" : r >= 55 ? "Moderate" : r >= 40 ? "Low" : "Crashed");
export const readinessNote = (r: number) =>
  r >= 75
    ? "Fully recovered — green light for a quality or volume block."
    : r >= 55
      ? "Hold intensity; full quality session viable in ~24h."
      : r >= 40
        ? "Prioritise easy volume or recovery; defer hard efforts."
        : "Recovery only — systemic and tissue load elevated.";
export const fatigueColor = (v: number) => (v >= 45 ? COLORS.hot : v >= 26 ? COLORS.warn : COLORS.good);
export const swatch = (c: string) =>
  c === COLORS.hot ? "rgba(255,138,92,.3)" : c === COLORS.warn ? "rgba(245,196,81,.26)" : "rgba(95,208,138,.22)";
export const swatchLite = (c: string) =>
  c === COLORS.hot ? "rgba(255,138,92,.13)" : c === COLORS.warn ? "rgba(245,196,81,.12)" : "rgba(95,208,138,.1)";
export const doseBarColor = (v: number) => (v > 6.5 ? COLORS.lime : v > 3.5 ? COLORS.warn : COLORS.teal);
export const conic = (color: string, pct: number) =>
  `conic-gradient(${color} 0 ${pct}%,rgba(255,255,255,.07) ${pct}% 100%)`;

// ---- Formatters ----
export const mmss = (x: number) => Math.floor(x / 60) + ":" + String(x % 60).padStart(2, "0");
export const fmtHMS = (sec: number) => {
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
};
export const fmtMin = (min: number) => {
  const t = Math.round(min * 60);
  return Math.floor(t / 60) + ":" + String(t % 60).padStart(2, "0");
};

// ---- Log Workout presets (session type → dose template) ----
export interface LogPreset {
  label: string;
  zone: string;
  dose: number[];
  dReady: number;
  dFat: number;
  cap: string;
  capBase: number;
}

export const PRESETS: Record<string, LogPreset> = {
  recovery: { label: "Recovery", zone: "Z1", dose: [3.0, 1.5, 2.0, 2.5, 1.5, 2.0], dReady: -5, dFat: 7, cap: "Aerobic", capBase: 0.3 },
  endurance: { label: "Endurance", zone: "Z2", dose: [6.5, 3.0, 3.5, 4.5, 2.0, 4.0], dReady: -10, dFat: 14, cap: "Aerobic", capBase: 0.9 },
  tempo: { label: "Tempo", zone: "Z3", dose: [5.5, 7.2, 6.0, 4.0, 2.2, 6.8], dReady: -16, dFat: 17, cap: "Aerobic", capBase: 1.4 },
  threshold: { label: "Threshold", zone: "Z4", dose: [5.0, 8.2, 6.5, 4.5, 2.6, 7.6], dReady: -19, dFat: 21, cap: "Glycolytic", capBase: 1.1 },
  intervals: { label: "VO₂ intervals", zone: "Z5", dose: [4.0, 9.2, 7.0, 5.5, 3.0, 8.4], dReady: -24, dFat: 26, cap: "Aerobic", capBase: 1.6 },
  long: { label: "Long run", zone: "Z2", dose: [8.8, 3.0, 3.0, 6.5, 2.2, 5.5], dReady: -14, dFat: 19, cap: "Work cap", capBase: 1.2 },
  strength: { label: "Strength", zone: "—", dose: [3.0, 6.0, 4.0, 2.0, 5.0, 3.0], dReady: -11, dFat: 13, cap: "Max strength", capBase: 0.8 },
};

export const DOSE_NAMES = ["Volume", "Intensity", "Density", "Impact", "Skill", "Metabolic"];

/** Project a logged session's six-axis dose and resulting S(t) shift. */
export function projectLogDose(p: { logType: string; rpe: number; durationMin: number; distanceKm: number; paceSec: number }) {
  const P = PRESETS[p.logType] || PRESETS.tempo;
  const f = p.rpe / 7;
  const cl = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));
  const paceMult = cl(290 / (p.paceSec || 290), 0.85, 1.2);
  const volume = cl(p.durationMin / 13, 0, 10);
  const intensity = cl(P.dose[1] * f * paceMult, 0, 10);
  const density = cl(P.dose[2] * (0.75 + 0.25 * f), 0, 10);
  const impact = cl(P.dose[3] * 0.4 + p.distanceKm * 0.4, 0, 10);
  const skill = P.dose[4];
  const metabolic = cl(P.dose[5] * f, 0, 10);
  const scaled = [volume, intensity, density, impact, skill, metabolic];
  const load = volume * 0.5 + intensity * 0.65 + impact * 0.3 + metabolic * 0.25;
  const readyAfter = cl(Math.round(64 - load * 1.65), 0, 100);
  const fatAfter = cl(Math.round(33 + load * 1.75), 0, 100);
  const capDelta = `+${((P.capBase * load) / 9.6).toFixed(1)}`;
  const readyColor = readyAfter >= 60 ? COLORS.good : readyAfter >= 45 ? COLORS.warn : COLORS.hot;
  return { scaled, load, readyAfter, fatAfter, capDelta, cap: P.cap, zone: P.zone, readyColor };
}

// ---- Capacity config (twin) ----
export interface CapCfg {
  key: string;
  label: string;
  max: number;
  base?: number;
  sub?: string;
}
export const CAP_CFG: CapCfg[] = [
  { key: "aerobic", label: "Aerobic", max: 400, base: 280 },
  { key: "glyco", label: "Glycolytic", max: 105, sub: "W′ proxy" },
  { key: "strength", label: "Max strength", max: 160, sub: "N·force" },
  { key: "power", label: "Power", max: 105, base: 52 },
  { key: "workcap", label: "Work cap", max: 103, sub: "structural" },
];
export const CAP_TIPS: Record<string, string> = {
  aerobic: "Aerobic capacity (model units) — sustainable oxidative output; grows with endurance volume. No fixed ceiling.",
  glyco: 'W′ proxy — anaerobic work capacity above threshold; the "matchbook" for hard surges and finishes.',
  strength: "Maximal force-producing capacity (N·force model units).",
  power: "Explosive output — force × velocity. Sprints, hills and surges.",
  workcap: "Structural work capacity — tolerance to total training volume before breakdown.",
};

export const SKILL_DEFS = [
  { label: "Running economy", base: 0.72 },
  { label: "Pacing discipline", base: 0.81 },
  { label: "Cadence", base: 0.68 },
  { label: "Hill technique", base: 0.55 },
  { label: "Descending", base: 0.6 },
  { label: "Fueling strategy", base: 0.74 },
];

/** Recent sessions used by the explainability drawer. */
export const RECENT_SESSIONS = [
  { n: "Tempo intervals · 5×6", d: "today", load: 61, rec: 1.0, imp: 0.7 },
  { n: "Long run · 22 km", d: "2 days ago", load: 78, rec: 0.55, imp: 1.1 },
  { n: "Threshold · 4×8 min", d: "4 days ago", load: 61, rec: 0.4, imp: 0.8 },
  { n: "Recovery jog · 8 km", d: "5 days ago", load: 24, rec: 0.5, imp: 0.5 },
];

// ---- Planning: week load vs readiness ----
export const PLAN_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const PLAN_LOAD = [24, 52, 61, 24, 68, 0, 78];
export const PLAN_READY = [70, 66, 64, 56, 58, 52, 66];

// ---- Live session interval plan ----
export interface SessionPhase {
  name: string;
  zone: string;
  pace: string;
  dur: number;
  rep?: number;
}
export function buildPhases(): SessionPhase[] {
  const phs: SessionPhase[] = [{ name: "Warm-up", zone: "Z1", pace: "5:40 /km", dur: 600 }];
  for (let i = 1; i <= 5; i++) {
    phs.push({ name: "Interval " + i, zone: "Z3", pace: "4:30 /km", dur: 360, rep: i });
    if (i < 5) phs.push({ name: "Float", zone: "Z1", pace: "5:40 /km", dur: 90 });
  }
  phs.push({ name: "Cool-down", zone: "Z1", pace: "5:40 /km", dur: 600 });
  return phs;
}
export const PHASES: SessionPhase[] = buildPhases();

// ---- Metric explainability drawer ----
export interface ExplainDriver {
  n: string;
  d: string;
  delta: string;
  color: string;
}
export interface ExplainContent {
  label: string;
  value: string | number;
  unit: string;
  def: string;
  flow: string;
  note: string;
  driversLabel: string;
  accent: string;
  drivers: ExplainDriver[];
}

export function buildExplain(key: string, D: TwinDay): ExplainContent {
  const [kind, nm = ""] = key.split(":");
  if (kind === "F") {
    const drivers: ExplainDriver[] = RECENT_SESSIONS.map((r) => ({ n: r.n, d: r.d, delta: `+${Math.max(1, Math.round(r.load * 0.18 * r.rec))}`, color: COLORS.hot }));
    drivers.push({ n: "Natural recovery", d: "rest + easy days", delta: "−28", color: COLORS.good });
    return {
      label: `${nm} fatigue`, value: D.F[nm], unit: "/ 100", accent: COLORS.hot,
      def: `Accumulated ${nm.toLowerCase()} fatigue — 0 fresh, 100 maxed. Each session adds load through its dose D(t); rest decays it.`,
      driversLabel: "Sessions that added load", drivers,
      flow: "Fₜ  =  Fₜ₋₁ · decay  +  D_intensity (t)",
      note: "Fatigue decays ~15–25% per day on easy or rest days — which is why recovery sessions pull this back down.",
    };
  }
  if (kind === "T") {
    return {
      label: `${nm} tissue load`, value: D.T[nm], unit: "/ 100", accent: COLORS.warn,
      def: `Local mechanical stress at the ${nm.toLowerCase()} — a readiness signal, not an injury reading. Driven mostly by impact and volume.`,
      driversLabel: "Sessions that loaded this region",
      drivers: RECENT_SESSIONS.map((r) => ({ n: r.n, d: r.d, delta: `+${Math.max(1, Math.round(r.load * 0.1 * r.rec * r.imp))}`, color: COLORS.warn })),
      flow: "Tₜ  =  Tₜ₋₁ · decay  +  D_impact (t)",
      note: "Tissue recovers slower than metabolic fatigue — keep impact capped while this stays elevated.",
    };
  }
  if (kind === "X") {
    const lm: Record<string, string> = { aerobic: "Aerobic capacity", glyco: "Glycolytic (W′)", strength: "Max strength", power: "Power", workcap: "Work capacity" };
    return {
      label: lm[nm] || nm, value: D.C[nm], unit: "model units", accent: COLORS.lime,
      def: "A trained capacity — it grows when adaptation signal and dose accumulate. Unlike fatigue, it persists.",
      driversLabel: "Sessions that built this",
      drivers: RECENT_SESSIONS.map((r) => ({ n: r.n, d: r.d, delta: `+${(r.load * 0.018 * r.rec).toFixed(1)}`, color: COLORS.teal })),
      flow: "Xₜ  =  Xₜ₋₁  +  s_signal · D (t)",
      note: "Capacity is durable — it decays only over weeks of detraining, not day to day.",
    };
  }
  if (kind === "FT") {
    if (nm === "vo2") {
      return {
        label: "VO₂max", value: "58.4", unit: "ml·kg⁻¹·min⁻¹", accent: COLORS.teal,
        def: "Estimated maximal aerobic power — your oxygen ceiling. Computed from the 1.5-mile time.",
        driversLabel: "Inputs that set it",
        drivers: [
          { n: "1.5 mi · 9:18", d: "primary driver", delta: "58.4", color: COLORS.teal },
          { n: "300 m · 0:52", d: "not used for VO₂", delta: "—", color: COLORS.faint },
          { n: "Age / sex", d: "not wired in v0.3", delta: "—", color: COLORS.faint },
        ],
        flow: "VO₂max  =  f ( 1.5 mi pace )",
        note: "A faster 1.5-mile lifts this directly. Age & sex correction arrives in a later model version.",
      };
    }
    return {
      label: "Speed ↔ Endurance", value: "−7.2", unit: "index", accent: COLORS.info,
      def: "Where you sit on the speed–endurance spectrum. Negative leans endurance. A style index, not a fitness score.",
      driversLabel: "How it is derived",
      drivers: [
        { n: "1.5 mi · 9:18", d: "relatively strong", delta: "endurance", color: COLORS.info },
        { n: "300 m · 0:52", d: "relatively weaker", delta: "− speed", color: COLORS.hot },
      ],
      flow: "profile  =  z ( 300 m )  −  z ( 1.5 mi )",
      note: "Sharpen the 300 m (strides, sprints) to pull this toward zero or positive.",
    };
  }
  if (kind === "PD") {
    const dm: Record<string, Omit<ExplainContent, "label" | "value" | "unit" | "accent" | "driversLabel"> & { v: string; a: string; drv: ExplainDriver[] }> = {
      Volume: { v: "5.5", a: COLORS.teal, def: "Total mechanical work in the session — set by duration and distance.", drv: [{ n: "42 min @ tempo", d: "duration", delta: "+3.5", color: COLORS.teal }, { n: "9.0 km", d: "distance", delta: "+2.0", color: COLORS.teal }], flow: "D_volume  =  f ( duration, distance )", note: "The biggest single lever on total load.", drivers: [] },
      Intensity: { v: "7.2", a: COLORS.lime, def: "How hard relative to threshold — set by target pace and prescribed RPE.", drv: [{ n: "4:30 /km · Zone 3", d: "tempo pace", delta: "+6.0", color: COLORS.lime }, { n: "RPE 7", d: "effort target", delta: "+1.2", color: COLORS.lime }], flow: "D_intensity  =  f ( pace ÷ threshold, RPE )", note: "Faster than threshold pushes this up steeply.", drivers: [] },
      Density: { v: "6.0", a: COLORS.lime, def: "Work-to-rest ratio across the intervals — how little recovery you get.", drv: [{ n: "6 min work / 90 s float", d: "4:1 ratio", delta: "+6.0", color: COLORS.lime }], flow: "D_density  =  work ÷ rest", note: "Shorter floats raise density without changing pace.", drivers: [] },
      Impact: { v: "4.0", a: COLORS.warn, def: "Mechanical pounding — foot strikes and ground forces. Drives tissue load.", drv: [{ n: "9.0 km on road", d: "distance × surface", delta: "+4.0", color: COLORS.warn }], flow: "D_impact  =  f ( distance, surface )", note: "This is what feeds knee and lumbar tissue load.", drivers: [] },
      Skill: { v: "2.2", a: COLORS.good, def: "Technical / neuromuscular demand of the session.", drv: [{ n: "Steady tempo", d: "low complexity", delta: "+2.2", color: COLORS.good }], flow: "D_skill  =  technical demand", note: "Intervals and hills raise this; steady runs keep it low.", drivers: [] },
      Metabolic: { v: "6.8", a: COLORS.lime, def: "Glycolytic + oxidative cost — the metabolic stress of the work.", drv: [{ n: "Zone 3 · 30 min work", d: "sustained near threshold", delta: "+6.8", color: COLORS.lime }], flow: "D_metabolic  =  f ( intensity, work time )", note: "Time spent near threshold is the main driver.", drivers: [] },
    };
    const o = dm[nm] || dm.Volume;
    return { label: `${nm} dose`, value: o.v, unit: "D(t) units", accent: o.a, def: o.def, driversLabel: "What set it", drivers: o.drv, flow: o.flow, note: o.note };
  }
  if (kind === "PI") {
    const im: Record<string, { l: string; v: string; u: string; a: string; def: string; drv: ExplainDriver[]; flow: string; note: string }> = {
      readiness: { l: "Readiness after", v: "48", u: "/ 100 (was 64)", a: COLORS.warn, def: "Projected readiness once this session's dose is applied to your twin.", drv: [{ n: "Current readiness", d: "now", delta: "64", color: COLORS.good }, { n: "Session load", d: "fatigue + tissue", delta: "−16", color: COLORS.hot }], flow: "R′  =  R  −  k · load ( D )", note: "A full quality day costs ~16 readiness; recovery before Friday is built in." },
      cns: { l: "CNS fatigue after", v: "52", u: "/ 100 (was 35)", a: COLORS.hot, def: "Projected central-nervous fatigue after the prescribed intervals.", drv: [{ n: "Current CNS", d: "now", delta: "35", color: COLORS.warn }, { n: "Intensity 7.2 × reps", d: "neural cost", delta: "+17", color: COLORS.hot }], flow: "CNS′  =  CNS  +  D_intensity · reps", note: "High-intensity intervals are CNS-expensive — hence the float recoveries." },
      aerobic: { l: "Aerobic drive", v: "+1.4", u: "model units", a: COLORS.lime, def: "Projected gain to aerobic capacity from this session's adaptive signal.", drv: [{ n: "Metabolic dose 6.8", d: "near-threshold time", delta: "+1.1", color: COLORS.teal }, { n: "Adaptation signal", d: "s_signal 2.5", delta: "+0.3", color: COLORS.teal }], flow: "ΔX  =  s_signal · D_metabolic", note: "Tempo work is the sweet spot for aerobic adaptation per unit fatigue." },
    };
    const o = im[nm] || im.readiness;
    return { label: o.l, value: o.v, unit: o.u, accent: o.a, def: o.def, driversLabel: "How it is projected", drivers: o.drv, flow: o.flow, note: o.note };
  }
  // readiness composite
  const meanF = Math.round(Object.values(D.F).reduce((a, b) => a + b, 0) / 6);
  const maxTk = Object.keys(D.T).sort((a, b) => D.T[b] - D.T[a])[0];
  return {
    label: "Readiness", value: D.readiness, unit: "/ 100", accent: COLORS.lime,
    def: "A composite freshness score — how recovered you are right now. Higher means more capacity for quality work.",
    driversLabel: "What pulls it down from 100",
    drivers: [
      { n: "Baseline (fully fresh)", d: "start", delta: "100", color: COLORS.good },
      { n: `Mean fatigue · F̄ = ${meanF}`, d: "× 0.55", delta: `−${Math.round(0.55 * meanF)}`, color: COLORS.hot },
      { n: `Peak tissue · ${maxTk} ${D.T[maxTk]}`, d: "× 0.45", delta: `−${Math.round(0.45 * D.T[maxTk])}`, color: COLORS.warn },
    ],
    flow: "R  =  100  −  0.55 · F̄  −  0.45 · Tₘₐₓ",
    note: "Raise readiness by lowering fatigue (recovery work) or letting peak tissue settle.",
  };
}
