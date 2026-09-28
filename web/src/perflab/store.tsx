// src/perflab/store.tsx
//
// State types, reducer and the usePerfLab hook for the Perf Lab "Performance
// OS". The <PerfLabProvider> component lives in PerfLabProvider.tsx (kept
// separate so this module exports no component — Fast Refresh friendly).
// Ported from the prototype's DCLogic component. Persists {ftDone, fresh}.

import { createContext, useContext } from "react";
import type { Dispatch } from "react";
// NOTE: sim.ts is imported for TYPES ONLY. The store used to value-import `PHASES`
// and index it inside the session reducer, which put a fabricated interval plan on
// the import path of every component that calls usePerfLab() — i.e. every screen.
// The phase durations now arrive with the openSession action, so the session state
// machine is generic and the fixture stays with the surface that renders it.
import type { CheckinState, SimParams } from "./sim";
import type { MetricsResponse, ReadinessScore, UnifiedStateVector } from "../types";

export type Screen =
  | "overview"
  | "assess"
  | "twin"
  | "planning"
  | "history"
  | "settings"
  | "objectives"
  | "simulate"
  | "onboarding";

export interface Settings {
  sex: string;
  units: string;
  accent: string;
  /** Training goal — feeds the prescriber (GET /v1/next-session?goal=…). Values
   *  are the exact strings the backend accepts; see TRAINING_GOALS. */
  goal: string;
  notifReadiness: boolean;
  notifTissue: boolean;
  notifWeekly: boolean;
}

/**
 * Selectable training goals. `value` is the exact string the prescriber accepts
 * (the GET /v1/next-session `goal` enum); `label` is the friendly display form.
 * Ordered general → strength family → conditioning → endurance so the neutral
 * default reads first. Not running-centric on purpose.
 */
/**
 * Simulator quick scenarios. Each sets volume + intensity + recovery together;
 * goal and horizon are left alone. Exported so the screen can show a preset as
 * active only when all three current values match it.
 */
export const SIM_PRESETS = {
  maintain: { volume: 48, intensity: "balanced", recovery: "standard" },
  build: { volume: 62, intensity: "balanced", recovery: "standard" },
  aggressive: { volume: 80, intensity: "hard", recovery: "minimal" },
} as const;
export type SimPresetName = keyof typeof SIM_PRESETS;

export const TRAINING_GOALS: { value: string; label: string }[] = [
  { value: "General", label: "General" },
  { value: "Strength", label: "Strength" },
  { value: "Hypertrophy", label: "Hypertrophy" },
  { value: "Power", label: "Power" },
  { value: "Powerlifting", label: "Powerlifting" },
  { value: "OlympicLifts", label: "Olympic lifts" },
  { value: "Calisthenics", label: "Calisthenics" },
  { value: "Gymnastics", label: "Gymnastics" },
  { value: "Grip", label: "Grip" },
  { value: "MetCon", label: "MetCon" },
  { value: "Running", label: "Running" },
  { value: "Sprinting", label: "Sprinting" },
  { value: "HalfMarathon", label: "Half marathon" },
  { value: "FullMarathon", label: "Full marathon" },
];

/** Neutral, non-specialized default — never assume a discipline up front. */
export const DEFAULT_GOAL = "General";

export const RUNNING_GOALS = new Set(["Running", "Sprinting", "HalfMarathon", "FullMarathon"]);
export const STRENGTH_GOALS = new Set(["Strength", "Hypertrophy", "Power", "Powerlifting", "OlympicLifts", "Calisthenics", "Gymnastics", "Grip"]);
export const isRunningGoal = (g: string) => RUNNING_GOALS.has(g);
export const isStrengthGoal = (g: string) => STRENGTH_GOALS.has(g);

export type Feel = "easy" | "controlled" | "hard" | "maxed";

export interface PerfLabState {
  screen: Screen;
  ftDone: boolean;
  /** Last field-test result (cached so VO₂/Profile survive navigation and feed the Twin). */
  fieldTest: MetricsResponse | null;
  /** Last state vector returned by log-workout (cached: backend has no GET /v1/state). */
  twinState: UnifiedStateVector | null;
  /** Last backend-owned readiness (GET /v1/readiness), cached after a check-in. */
  readiness: ReadinessScore | null;
  obStep: number;
  authOpen: boolean;
  logOpen: boolean;
  logType: string;
  /** The workout-log draft readings. `null` means THE ATHLETE HAS NOT ENTERED IT.
   *  Never a seeded number standing in for "unknown" — that is the whole point:
   *  these four fed `buildWorkoutLog` directly, so an untouched modal used to
   *  submit 42 min / 9 km / RPE 7 to the real backend. Sample values for the
   *  guest preview live in the fixture module (`sim.SAMPLE_LOG_DRAFT`), which is
   *  where fixtures belong and where the submit path cannot reach them. */
  rpe: number | null;
  logApplied: boolean;
  durationMin: number | null;
  distanceKm: number | null;
  paceSec: number | null;
  /** GUEST-preview scrub index over the deterministic sim `DAYS` only. Never the
   *  cross-screen contract for the live twin — that is `selectedTwinSnapshotId`. */
  twinDayIdx: number | null;
  /** Cross-screen selection for the LIVE twin: the persisted AthleteState row id
   *  (StateHistorySnapshotRead.snapshot_id), NOT a list index — the state-history
   *  window shifts as rows accrue, so position is not a durable reference. null =
   *  select the newest recorded snapshot. Set by History→Twin deep-links and by
   *  the Twin time-travel scrub. */
  selectedTwinSnapshotId: number | null;
  navCollapsed: boolean;
  fresh: boolean;
  sessOpen: boolean;
  phaseIdx: number;
  sessRemaining: number;
  sessRunning: boolean;
  sessDone: boolean;
  /** Durations (seconds) of the phases the open session is stepping through,
   *  supplied by whoever opened it. Empty when no session is open. */
  sessPhaseDurations: number[];
  settings: Settings;
  explainOpen: boolean;
  explainKey: string | null;
  capView: "bars" | "radar";
  sim: SimParams;
  checkin: CheckinState;
  checkinOpen: boolean;
  feedbackOpen: boolean;
  feedbackApplied: boolean;
  /** The planned session the feedback overlay is reporting on. Null means the
   *  guest preview, which has no real session behind it — the overlay uses this
   *  to decide whether it can write anything at all. */
  feedbackSessionId: number | null;
  /** Bumped after feedback is recorded so the week strip re-reads session state. */
  feedbackRefreshKey: number;
  feel: Feel;
  /** Block-creation overlay (POST /v1/planning/blocks) — see BlockCreateModal. */
  blockCreateOpen: boolean;
  /** Bumped after a block is created so PlanningScreen's useAuthedResource re-fetches. */
  planningRefreshKey: number;
  /** ISO date ("YYYY-MM-DD") anchoring the week PlanningScreen shows; null = current
   *  week. Set to a new block's start_date so its first week is what we display. */
  planningWeekAnchor: string | null;
  /** New-objective overlay (POST /v1/objectives) — see ObjectiveCreateModal. */
  objectiveCreateOpen: boolean;
  /** Bumped after an objective is created/updated/deleted so any screen reading
   *  the objectives list (ObjectivesScreen, the Overview summary) re-fetches. */
  objectivesRefreshKey: number;
  /** New-macrocycle overlay (POST /v1/macrocycles) — see MacrocycleCreateModal. */
  macrocycleCreateOpen: boolean;
  /** Bumped after a macrocycle is created/updated/deleted so any screen reading
   *  the macrocycles list (Objectives' Program section, the Overview week X of Y)
   *  re-fetches. */
  macrocyclesRefreshKey: number;
  /** Bumped after a check-in so any screen reading backend readiness + the latest
   *  wellness sample (the Overview ring / tiles / sparkline) re-fetches. */
  readinessRefreshKey: number;
}

interface Persisted {
  ftDone: boolean;
  fresh: boolean;
  fieldTest: MetricsResponse | null;
  twinState: UnifiedStateVector | null;
  readiness: ReadinessScore | null;
  /** Client-only UI preferences (units, accent, notifications…). The athlete's
   *  performance profile lives in the backend (GET/PATCH /v1/profile), not here. */
  settings: Settings;
}

export const STORAGE_KEY = "perflab_v1";

function loadPersisted(): Partial<Persisted> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

export function initialState(): PerfLabState {
  const sv = loadPersisted();
  return {
    screen: "overview",
    ftDone: typeof sv.ftDone === "boolean" ? sv.ftDone : false,
    fieldTest: sv.fieldTest ?? null,
    twinState: sv.twinState ?? null,
    readiness: sv.readiness ?? null,
    obStep: 1,
    authOpen: false,
    logOpen: false,
    logType: "tempo",
    rpe: null,
    logApplied: false,
    durationMin: null,
    distanceKm: null,
    paceSec: null,
    twinDayIdx: null,
    selectedTwinSnapshotId: null,
    navCollapsed: false,
    fresh: typeof sv.fresh === "boolean" ? sv.fresh : false,
    sessOpen: false,
    phaseIdx: 0,
    sessRemaining: 600,
    sessPhaseDurations: [],
    sessRunning: false,
    sessDone: false,
    settings: {
      sex: "Female",
      units: "Metric (km)",
      accent: "#c6f135",
      goal: DEFAULT_GOAL,
      notifReadiness: true,
      notifTissue: true,
      notifWeekly: false,
      ...(sv.settings ?? {}),
    },
    explainOpen: false,
    explainKey: null,
    capView: "bars",
    sim: { volume: 56, intensity: "balanced", weeks: 8, recovery: "standard", goal: DEFAULT_GOAL },
    // #199: EVERY check-in field starts "not reported" (null). It used to be seeded
    // with sample constants (hrv 64, sleepH 7.5, sleepQ 4, rhr 52, soreness "mild",
    // mood 4, stress 2), and `sleepQ`/`mood` were read straight out of here by
    // LogWorkoutModal and submitted to authenticated POST /v1/log-workout as if the
    // athlete had reported them. Gating on `done` would not have fixed it: `done` is
    // one boolean over six independent signals, so moving a single slider still
    // submitted the other five seeds with `done === true`. The only representation of
    // "unknown" that cannot be mistaken for a measurement is the absence of a value.
    checkin: { hrv: null, sleepH: null, sleepQ: null, rhr: null, soreness: null, mood: null, stress: null, done: false },
    checkinOpen: false,
    feedbackOpen: false,
    feedbackApplied: false,
    feedbackSessionId: null,
    feedbackRefreshKey: 0,
    feel: "controlled",
    blockCreateOpen: false,
    planningRefreshKey: 0,
    planningWeekAnchor: null,
    objectiveCreateOpen: false,
    objectivesRefreshKey: 0,
    macrocycleCreateOpen: false,
    macrocyclesRefreshKey: 0,
    readinessRefreshKey: 0,
  };
}

export type Action =
  | { type: "merge"; patch: Partial<PerfLabState> }
  | { type: "mergeFn"; fn: (s: PerfLabState) => Partial<PerfLabState> }
  | { type: "mergeSettings"; patch: Partial<Settings> }
  | { type: "mergeCheckin"; patch: Partial<CheckinState> }
  | { type: "mergeSim"; patch: Partial<SimParams> }
  | { type: "openSession"; phaseDurations: number[] }
  | { type: "sessSkip" }
  | { type: "sessToggle" }
  | { type: "tick" };

export function reducer(state: PerfLabState, action: Action): PerfLabState {
  switch (action.type) {
    case "merge":
      return { ...state, ...action.patch };
    case "mergeFn":
      return { ...state, ...action.fn(state) };
    case "mergeSettings":
      return { ...state, settings: { ...state.settings, ...action.patch } };
    case "mergeCheckin":
      return { ...state, checkin: { ...state.checkin, ...action.patch } };
    case "mergeSim":
      return { ...state, sim: { ...state.sim, ...action.patch } };
    case "openSession": {
      const durations = action.phaseDurations;
      return {
        ...state,
        sessOpen: true,
        phaseIdx: 0,
        sessPhaseDurations: durations,
        sessRemaining: durations[0] ?? 0,
        sessRunning: false,
        sessDone: false,
      };
    }
    case "sessToggle":
      if (state.sessRunning) return { ...state, sessRunning: false };
      if (state.sessDone)
        return {
          ...state,
          phaseIdx: 0,
          sessRemaining: state.sessPhaseDurations[0] ?? 0,
          sessRunning: false,
          sessDone: false,
        };
      return { ...state, sessRunning: true };
    case "sessSkip":
    case "tick": {
      if (action.type === "tick" && state.sessRemaining > 1) {
        return { ...state, sessRemaining: state.sessRemaining - 1 };
      }
      if (state.phaseIdx < state.sessPhaseDurations.length - 1) {
        const next = state.phaseIdx + 1;
        return { ...state, phaseIdx: next, sessRemaining: state.sessPhaseDurations[next] };
      }
      return { ...state, sessRunning: false, sessDone: true };
    }
    default:
      return state;
  }
}

export interface PerfLabActions {
  setScreen: (s: Screen) => void;
  openAuth: () => void;
  closeAuth: () => void;
  ftCompute: (result: MetricsResponse) => void;
  ftRecompute: () => void;
  cacheTwinState: (sv: UnifiedStateVector) => void;
  cacheReadiness: (r: ReadinessScore | null) => void;
  seedTwin: () => void;
  obNext: () => void;
  obBack: () => void;
  openLog: () => void;
  closeLog: () => void;
  applyLog: () => void;
  /** `null` clears the reading back to "not entered". */
  setRpe: (n: number | null) => void;
  setLogType: (k: string) => void;
  setDur: (n: number | null) => void;
  setDist: (n: number | null) => void;
  setPaceSec: (n: number | null) => void;
  setTwinDay: (i: number) => void;
  /** Select a live twin snapshot by its persisted row id (null = newest). */
  setSelectedTwinSnapshot: (id: number | null) => void;
  setCapView: (v: "bars" | "radar") => void;
  /** Open the session player over an explicit phase plan (durations in seconds). */
  openSession: (phaseDurations: number[]) => void;
  closeSession: () => void;
  sessToggle: () => void;
  sessSkip: () => void;
  sessToLog: () => void;
  openCheckin: () => void;
  closeCheckin: () => void;
  applyCheckin: () => void;
  setCheckin: (patch: Partial<CheckinState>) => void;
  openExplain: (key: string) => void;
  closeExplain: () => void;
  setSim: (patch: Partial<SimParams>) => void;
  simPreset: (name: SimPresetName) => void;
  /** Report on a real planned session. The id is required: without it there is
   *  nothing to write to, and inferring one from what happens to be on screen is
   *  how feedback lands on the wrong session. */
  openFeedback: (plannedSessionId: number) => void;
  closeFeedback: () => void;
  applyFeedback: () => void;
  feedbackToTwin: () => void;
  /** Bump after feedback is recorded so dependents re-fetch. */
  refreshFeedback: () => void;
  setFeel: (feel: Feel, rpe: number) => void;
  setSetting: <K extends keyof Settings>(key: K, value: Settings[K]) => void;
  toggleNav: () => void;
  toggleFresh: () => void;
  openBlockCreate: () => void;
  closeBlockCreate: () => void;
  /** Called after a block is created: focus the week containing `startDateIso`
   *  and force a re-fetch (covers creating a block inside the current week too). */
  focusPlanningWeek: (startDateIso: string) => void;
  openObjectiveCreate: () => void;
  closeObjectiveCreate: () => void;
  /** Bump after any objective create/update/delete so dependents re-fetch. */
  refreshObjectives: () => void;
  openMacrocycleCreate: () => void;
  closeMacrocycleCreate: () => void;
  /** Bump after any macrocycle create/update/delete so dependents re-fetch. */
  refreshMacrocycles: () => void;
  /** Bump after a check-in so backend readiness + latest wellness re-fetch. */
  refreshReadiness: () => void;
}

export interface PerfLabContextValue {
  state: PerfLabState;
  actions: PerfLabActions;
}

/** Build the actions object from a dispatch fn (used by the provider). */
export function buildActions(dispatch: Dispatch<Action>): PerfLabActions {
  const merge = (patch: Partial<PerfLabState>) => dispatch({ type: "merge", patch });
  const mergeFn = (fn: (s: PerfLabState) => Partial<PerfLabState>) => dispatch({ type: "mergeFn", fn });
  return {
    setScreen: (s) => merge({ screen: s }),
    openAuth: () => merge({ authOpen: true }),
    closeAuth: () => merge({ authOpen: false }),
    ftCompute: (result) => merge({ ftDone: true, fieldTest: result }),
    ftRecompute: () => merge({ ftDone: false, fieldTest: null }),
    cacheTwinState: (sv) => merge({ twinState: sv }),
    cacheReadiness: (r) => merge({ readiness: r }),
    seedTwin: () => merge({ ftDone: true, fresh: false, screen: "twin" }),
    obNext: () => mergeFn((s) => ({ obStep: Math.min(3, s.obStep + 1) })),
    obBack: () => mergeFn((s) => ({ obStep: Math.max(1, s.obStep - 1) })),
    // Opening the log CLEARS the draft. Without this, the readings from a session
    // already logged stay in the store, and reopening + Apply silently re-submits
    // them as if they were entered for the new session.
    openLog: () =>
      merge({
        logOpen: true,
        logApplied: false,
        rpe: null,
        durationMin: null,
        distanceKm: null,
        paceSec: null,
      }),
    closeLog: () => merge({ logOpen: false }),
    applyLog: () => merge({ logOpen: false, logApplied: true, screen: "twin" }),
    setRpe: (n) => merge({ rpe: n }),
    setLogType: (k) => merge({ logType: k }),
    setDur: (n) => merge({ durationMin: n }),
    setDist: (n) => merge({ distanceKm: n }),
    setPaceSec: (n) => merge({ paceSec: n }),
    setTwinDay: (i) => merge({ twinDayIdx: i }),
    setSelectedTwinSnapshot: (id) => merge({ selectedTwinSnapshotId: id }),
    setCapView: (v) => merge({ capView: v }),
    openSession: (phaseDurations) => dispatch({ type: "openSession", phaseDurations }),
    closeSession: () => merge({ sessOpen: false, sessRunning: false }),
    sessToggle: () => dispatch({ type: "sessToggle" }),
    sessSkip: () => dispatch({ type: "sessSkip" }),
    // Guest demo path only (SessionPlayer is guest-only). `feedbackSessionId`
    // stays null so the overlay knows there is no real session to report on and
    // renders the preview rather than a form that could not submit.
    sessToLog: () =>
      merge({
        sessOpen: false,
        sessRunning: false,
        feedbackOpen: true,
        feedbackApplied: false,
        feedbackSessionId: null,
      }),
    openCheckin: () => merge({ checkinOpen: true }),
    closeCheckin: () => merge({ checkinOpen: false }),
    applyCheckin: () =>
      mergeFn((s) => ({
        checkinOpen: false,
        checkin: { ...s.checkin, done: true },
        // A completed check-in changes backend readiness + the latest wellness
        // sample — bump so the Overview ring / tiles / sparkline re-fetch.
        readinessRefreshKey: s.readinessRefreshKey + 1,
      })),
    setCheckin: (patch) => dispatch({ type: "mergeCheckin", patch }),
    openExplain: (key) => merge({ explainOpen: true, explainKey: key }),
    closeExplain: () => merge({ explainOpen: false }),
    setSim: (patch) => dispatch({ type: "mergeSim", patch }),
    simPreset: (name) => dispatch({ type: "mergeSim", patch: SIM_PRESETS[name] }),
    openFeedback: (plannedSessionId) =>
      merge({ feedbackOpen: true, feedbackApplied: false, feedbackSessionId: plannedSessionId }),
    closeFeedback: () => merge({ feedbackOpen: false, feedbackSessionId: null }),
    applyFeedback: () => merge({ feedbackApplied: true }),
    feedbackToTwin: () =>
      merge({ feedbackOpen: false, feedbackApplied: false, feedbackSessionId: null, screen: "twin" }),
    refreshFeedback: () => mergeFn((s) => ({ feedbackRefreshKey: s.feedbackRefreshKey + 1 })),
    setFeel: (feel, rpe) => merge({ feel, rpe }),
    setSetting: (key, value) => dispatch({ type: "mergeSettings", patch: { [key]: value } as Partial<Settings> }),
    toggleNav: () => mergeFn((s) => ({ navCollapsed: !s.navCollapsed })),
    toggleFresh: () => mergeFn((s) => ({ fresh: !s.fresh })),
    openBlockCreate: () => merge({ blockCreateOpen: true }),
    closeBlockCreate: () => merge({ blockCreateOpen: false }),
    focusPlanningWeek: (startDateIso) =>
      mergeFn((s) => ({ planningWeekAnchor: startDateIso, planningRefreshKey: s.planningRefreshKey + 1 })),
    openObjectiveCreate: () => merge({ objectiveCreateOpen: true }),
    closeObjectiveCreate: () => merge({ objectiveCreateOpen: false }),
    refreshObjectives: () => mergeFn((s) => ({ objectivesRefreshKey: s.objectivesRefreshKey + 1 })),
    openMacrocycleCreate: () => merge({ macrocycleCreateOpen: true }),
    closeMacrocycleCreate: () => merge({ macrocycleCreateOpen: false }),
    refreshMacrocycles: () => mergeFn((s) => ({ macrocyclesRefreshKey: s.macrocyclesRefreshKey + 1 })),
    refreshReadiness: () => mergeFn((s) => ({ readinessRefreshKey: s.readinessRefreshKey + 1 })),
  };
}

export const PerfLabContext = createContext<PerfLabContextValue | null>(null);

export function usePerfLab(): PerfLabContextValue {
  const ctx = useContext(PerfLabContext);
  if (!ctx) throw new Error("usePerfLab must be used within <PerfLabProvider>");
  return ctx;
}
