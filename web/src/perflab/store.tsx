// src/perflab/store.tsx
//
// State types, reducer and the usePerfLab hook for the Perf Lab "Performance
// OS". The <PerfLabProvider> component lives in PerfLabProvider.tsx (kept
// separate so this module exports no component — Fast Refresh friendly).
// Ported from the prototype's DCLogic component. Persists {ftDone, fresh}.

import { createContext, useContext } from "react";
import type { Dispatch } from "react";
import { DAY_COUNT, PHASES } from "./sim";
import type { CheckinState, SimParams } from "./sim";
import type { MetricsResponse, UnifiedStateVector } from "../types";

export type Screen =
  | "overview"
  | "field"
  | "twin"
  | "planning"
  | "history"
  | "settings"
  | "race"
  | "simulate"
  | "onboarding";

export interface Settings {
  sex: string;
  units: string;
  accent: string;
  sport: string;
  notifReadiness: boolean;
  notifTissue: boolean;
  notifWeekly: boolean;
}

export interface RaceState {
  name: string;
  loc: string;
  dateLabel: string;
  distName: string;
  distKm: number;
  goalSec: number;
  daysToGo: number;
}

export type Feel = "easy" | "controlled" | "hard" | "maxed";

export interface PerfLabState {
  screen: Screen;
  ftDone: boolean;
  /** Last field-test result (cached so VO₂/Profile survive navigation and feed the Twin). */
  fieldTest: MetricsResponse | null;
  /** Last state vector returned by log-workout (cached: backend has no GET /v1/state). */
  twinState: UnifiedStateVector | null;
  obStep: number;
  authOpen: boolean;
  logOpen: boolean;
  logType: string;
  rpe: number;
  logApplied: boolean;
  durationMin: number;
  distanceKm: number;
  paceSec: number;
  twinDayIdx: number | null;
  navCollapsed: boolean;
  fresh: boolean;
  sessOpen: boolean;
  phaseIdx: number;
  sessRemaining: number;
  sessRunning: boolean;
  sessDone: boolean;
  settings: Settings;
  explainOpen: boolean;
  explainKey: string | null;
  capView: "bars" | "radar";
  sim: SimParams;
  race: RaceState;
  checkin: CheckinState;
  checkinOpen: boolean;
  feedbackOpen: boolean;
  feedbackApplied: boolean;
  feel: Feel;
}

interface Persisted {
  ftDone: boolean;
  fresh: boolean;
  fieldTest: MetricsResponse | null;
  twinState: UnifiedStateVector | null;
}

export const STORAGE_KEY = "perflab_v1";
export const LAST = DAY_COUNT - 1;

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
    obStep: 1,
    authOpen: false,
    logOpen: false,
    logType: "tempo",
    rpe: 7,
    logApplied: false,
    durationMin: 42,
    distanceKm: 9,
    paceSec: 278,
    twinDayIdx: null,
    navCollapsed: false,
    fresh: typeof sv.fresh === "boolean" ? sv.fresh : false,
    sessOpen: false,
    phaseIdx: 0,
    sessRemaining: 600,
    sessRunning: false,
    sessDone: false,
    settings: {
      sex: "Female",
      units: "Metric (km)",
      accent: "#c6f135",
      sport: "Distance",
      notifReadiness: true,
      notifTissue: true,
      notifWeekly: false,
    },
    explainOpen: false,
    explainKey: null,
    capView: "bars",
    sim: { volume: 56, intensity: "balanced", weeks: 8, recovery: "standard" },
    race: {
      name: "Valencia Marathon",
      loc: "Valencia, ES",
      dateLabel: "Dec 6, 2026",
      distName: "Marathon",
      distKm: 42.195,
      goalSec: 10500,
      daysToGo: 116,
    },
    checkin: { hrv: 64, sleepH: 7.5, sleepQ: 4, rhr: 52, soreness: "mild", mood: 4, done: false },
    checkinOpen: false,
    feedbackOpen: false,
    feedbackApplied: false,
    feel: "controlled",
  };
}

export type Action =
  | { type: "merge"; patch: Partial<PerfLabState> }
  | { type: "mergeFn"; fn: (s: PerfLabState) => Partial<PerfLabState> }
  | { type: "mergeSettings"; patch: Partial<Settings> }
  | { type: "mergeCheckin"; patch: Partial<CheckinState> }
  | { type: "mergeSim"; patch: Partial<SimParams> }
  | { type: "openSession" }
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
    case "openSession":
      return { ...state, sessOpen: true, phaseIdx: 0, sessRemaining: PHASES[0].dur, sessRunning: false, sessDone: false };
    case "sessToggle":
      if (state.sessRunning) return { ...state, sessRunning: false };
      if (state.sessDone) return { ...state, phaseIdx: 0, sessRemaining: PHASES[0].dur, sessRunning: false, sessDone: false };
      return { ...state, sessRunning: true };
    case "sessSkip":
    case "tick": {
      if (action.type === "tick" && state.sessRemaining > 1) {
        return { ...state, sessRemaining: state.sessRemaining - 1 };
      }
      if (state.phaseIdx < PHASES.length - 1) {
        const next = state.phaseIdx + 1;
        return { ...state, phaseIdx: next, sessRemaining: PHASES[next].dur };
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
  seedTwin: () => void;
  obNext: () => void;
  obBack: () => void;
  openLog: () => void;
  closeLog: () => void;
  applyLog: () => void;
  setRpe: (n: number) => void;
  setLogType: (k: string) => void;
  setDur: (n: number) => void;
  setDist: (n: number) => void;
  setPaceSec: (n: number) => void;
  setTwinDay: (i: number) => void;
  dayPrev: () => void;
  dayNext: () => void;
  dayToday: () => void;
  setCapView: (v: "bars" | "radar") => void;
  openSession: () => void;
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
  simPreset: (name: "maintain" | "build" | "aggressive") => void;
  openFeedback: () => void;
  closeFeedback: () => void;
  applyFeedback: () => void;
  feedbackToTwin: () => void;
  setFeel: (feel: Feel, rpe: number) => void;
  setSetting: <K extends keyof Settings>(key: K, value: Settings[K]) => void;
  toggleNav: () => void;
  toggleFresh: () => void;
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
    seedTwin: () => merge({ ftDone: true, fresh: false, screen: "twin" }),
    obNext: () => mergeFn((s) => ({ obStep: Math.min(3, s.obStep + 1) })),
    obBack: () => mergeFn((s) => ({ obStep: Math.max(1, s.obStep - 1) })),
    openLog: () => merge({ logOpen: true, logApplied: false }),
    closeLog: () => merge({ logOpen: false }),
    applyLog: () => merge({ logOpen: false, logApplied: true, screen: "twin" }),
    setRpe: (n) => merge({ rpe: n }),
    setLogType: (k) => merge({ logType: k }),
    setDur: (n) => merge({ durationMin: n }),
    setDist: (n) => merge({ distanceKm: n }),
    setPaceSec: (n) => merge({ paceSec: n }),
    setTwinDay: (i) => merge({ twinDayIdx: i }),
    dayPrev: () => mergeFn((s) => ({ twinDayIdx: Math.max(0, (s.twinDayIdx == null ? LAST : s.twinDayIdx) - 1) })),
    dayNext: () => mergeFn((s) => ({ twinDayIdx: Math.min(LAST, (s.twinDayIdx == null ? LAST : s.twinDayIdx) + 1) })),
    dayToday: () => merge({ twinDayIdx: LAST }),
    setCapView: (v) => merge({ capView: v }),
    openSession: () => dispatch({ type: "openSession" }),
    closeSession: () => merge({ sessOpen: false, sessRunning: false }),
    sessToggle: () => dispatch({ type: "sessToggle" }),
    sessSkip: () => dispatch({ type: "sessSkip" }),
    sessToLog: () => merge({ sessOpen: false, sessRunning: false, feedbackOpen: true, feedbackApplied: false }),
    openCheckin: () => merge({ checkinOpen: true }),
    closeCheckin: () => merge({ checkinOpen: false }),
    applyCheckin: () => mergeFn((s) => ({ checkinOpen: false, checkin: { ...s.checkin, done: true } })),
    setCheckin: (patch) => dispatch({ type: "mergeCheckin", patch }),
    openExplain: (key) => merge({ explainOpen: true, explainKey: key }),
    closeExplain: () => merge({ explainOpen: false }),
    setSim: (patch) => dispatch({ type: "mergeSim", patch }),
    simPreset: (name) =>
      dispatch({
        type: "mergeSim",
        patch:
          name === "maintain"
            ? { volume: 48, intensity: "balanced", recovery: "standard" }
            : name === "build"
              ? { volume: 62, intensity: "balanced", recovery: "standard" }
              : { volume: 80, intensity: "hard", recovery: "minimal" },
      }),
    openFeedback: () => merge({ feedbackOpen: true, feedbackApplied: false }),
    closeFeedback: () => merge({ feedbackOpen: false }),
    applyFeedback: () => merge({ feedbackApplied: true }),
    feedbackToTwin: () => merge({ feedbackOpen: false, feedbackApplied: false, screen: "twin" }),
    setFeel: (feel, rpe) => merge({ feel, rpe }),
    setSetting: (key, value) => dispatch({ type: "mergeSettings", patch: { [key]: value } as Partial<Settings> }),
    toggleNav: () => mergeFn((s) => ({ navCollapsed: !s.navCollapsed })),
    toggleFresh: () => mergeFn((s) => ({ fresh: !s.fresh })),
  };
}

export const PerfLabContext = createContext<PerfLabContextValue | null>(null);

export function usePerfLab(): PerfLabContextValue {
  const ctx = useContext(PerfLabContext);
  if (!ctx) throw new Error("usePerfLab must be used within <PerfLabProvider>");
  return ctx;
}
