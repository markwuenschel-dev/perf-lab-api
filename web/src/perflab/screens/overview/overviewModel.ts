// src/perflab/screens/overview/overviewModel.ts
//
// The authority seam for authenticated Overview (wayfinder map #182, L7):
//
//     canonical resource states -> pure presentation model -> dumb sections
//
// Every selector here owns *presentation interpretation, not physiology*. None of
// them computes an athlete measurement: readiness is whatever the backend said
// (PDR-0005), the trend is a display proxy over recorded state, and a value that
// is missing becomes an explicit empty or unavailable state rather than a
// plausible-looking number (ADR-0046 — `0` never means unknown).
//
// The module takes ONLY canonical resources. It has no parameter through which a
// check-in draft, a fixture, or any other client-side value could reach a
// rendered metric — which is what makes the noninterference property (#189c)
// structural rather than a thing reviewers have to keep noticing.
import { assertNever, type AuthedResource } from "../../resource";
import { meanFatigue, peakTissue, snapshotCapacity, fatigueDisplayProxy } from "../../stateVector";
import type {
  OverviewMetrics,
  ReadinessScore,
  StateHistorySnapshotRead,
  TrainingLoadMetrics,
  WellnessSampleOut,
} from "@/types";

// ---- The honesty types (map #182, L2) -----------------------------------------
// `AuthenticatedMetric` is reproduced exactly as L2 locked it: three arms, none of
// which can hold a preview value. A guest preview value is a different type
// entirely and lives in the guest component tree, so no widening or cast can move
// one into an authenticated view.

/** Why a metric legitimately has nothing to show, despite a successful load. */
export type EmptyReason =
  /** The collection loaded and is empty. */
  | "no_data"
  /** Loaded, but too few points for the reduction to mean anything. */
  | "insufficient_history"
  /** The athlete has no modeled state yet (readiness score is null). */
  | "no_state_yet"
  /** The row exists but this particular field was not recorded. */
  | "not_recorded";

/** Why a metric could not be produced at all. */
export type UnavailableReason = "load_failed" | "not_authenticated";

export type AuthenticatedMetric<T> =
  | { kind: "value"; value: T }
  | { kind: "empty"; reason: EmptyReason }
  | { kind: "unavailable"; reason: UnavailableReason };

/**
 * A metric plus the in-flight state.
 *
 * L2's three arms are kept verbatim; "loading" is added *beside* them rather than
 * folded into one of them, because collapsing it into `unavailable` would report a
 * pending request as a failure, and collapsing it into `empty` would assert the
 * athlete has no data before anyone knows.
 */
export type MetricState<T> = AuthenticatedMetric<T> | { kind: "loading" };

const LOADING: MetricState<never> = { kind: "loading" };
const NOT_AUTHED: MetricState<never> = { kind: "unavailable", reason: "not_authenticated" };
const LOAD_FAILED: MetricState<never> = { kind: "unavailable", reason: "load_failed" };

/**
 * Fold a canonical resource into a metric state, delegating only the success arm.
 *
 * Exhaustive over `AuthedResource`, so a new arm on the union breaks compilation
 * here instead of silently falling through to a fabricated default.
 */
function fold<T, R>(
  resource: AuthedResource<T>,
  project: (data: T) => MetricState<R>,
): MetricState<R> {
  switch (resource.status) {
    case "guest":
      // Authenticated selectors are not rendered for guests — the guest branch is a
      // separate component tree — but the function stays total, and the honest
      // answer for "no authenticated request authority" is unavailable, not empty.
      return NOT_AUTHED;
    case "loading":
      return LOADING;
    case "error":
      return LOAD_FAILED;
    case "success":
      return project(resource.data);
    default:
      return assertNever(resource);
  }
}

/** A nullable backend number becomes a value or an explicit empty — never 0. */
function fromNullable<T>(value: T | null | undefined, reason: EmptyReason): MetricState<T> {
  return value == null ? { kind: "empty", reason } : { kind: "value", value };
}

// ---- Readiness (PDR-0005) ------------------------------------------------------

export interface ReadinessView {
  /** The backend's number, rounded for display. Never computed here. */
  score: MetricState<number>;
  /** Backend confidence band (ADR-0059), only ever alongside a real score. */
  confidenceBand: MetricState<string>;
}

/**
 * Readiness is backend-owned (PDR-0005, ADR-0026). This selector may expose the
 * number, or say it is unavailable / not yet established — it must never derive
 * one. The absence of any wellness or check-in parameter is the enforcement:
 * there is no input through which a local signal could change the result (#189c).
 */
export function readinessSection(resource: AuthedResource<ReadinessScore>): ReadinessView {
  const score = fold(resource, (data) =>
    fromNullable(data.score == null ? null : Math.round(data.score), "no_state_yet"),
  );
  // A confidence band describes a score; without one it describes nothing, so it
  // is suppressed rather than shown against an empty ring.
  const confidenceBand: MetricState<string> =
    score.kind === "value"
      ? fold(resource, (data) => fromNullable(data.confidence?.band ?? null, "not_recorded"))
      : score.kind === "loading"
        ? LOADING
        : { kind: "empty", reason: "not_recorded" };
  return { score, confidenceBand };
}

// ---- 14-day trend (map #182, #186) ---------------------------------------------

/**
 * Locked label. The series is `100 − meanFatigue` (`stateVector.fatigueDisplayProxy`),
 * which is NOT canonical readiness — #186 ruled it must never be labelled
 * "readiness", and must not sit under or imply continuity with the backend score.
 */
export const TREND_LABEL = "Fatigue-derived trend" as const;

export interface TrendView {
  label: typeof TREND_LABEL;
  series: MetricState<number[]>;
  /** Newest minus oldest, only when a real series exists. */
  delta: MetricState<number>;
}

/**
 * `/state-history` returns oldest→newest (`state_service.load_recent_state_snapshots`
 * reverses the newest-first repository query), so the series is already in render
 * order and the delta is `last − first`.
 *
 * A single row is `insufficient_history`, not a flat line: #186 recorded that the
 * old code drew 14 fabricated points for 0 rows while showing "not enough history"
 * for 1 — less evidence producing a more confident render.
 */
export function trendSection(resource: AuthedResource<StateHistorySnapshotRead[]>): TrendView {
  const series = fold<StateHistorySnapshotRead[], number[]>(resource, (rows) => {
    if (rows.length === 0) return { kind: "empty", reason: "no_data" };
    if (rows.length < 2) return { kind: "empty", reason: "insufficient_history" };
    return { kind: "value", value: rows.map(fatigueDisplayProxy) };
  });
  const delta: MetricState<number> =
    series.kind === "value"
      ? { kind: "value", value: series.value[series.value.length - 1] - series.value[0] }
      : series;
  return { label: TREND_LABEL, series, delta };
}

// ---- "This morning" wellness ---------------------------------------------------

export type MorningMetricKey = "hrv" | "sleep" | "resting_hr" | "soreness";

export interface MorningMetric {
  key: MorningMetricKey;
  label: string;
  /** Pre-formatted display string, so the leaf component stays dumb. */
  value: MetricState<string>;
}

export interface MorningView {
  metrics: MorningMetric[];
  /**
   * The date of the most recent sample, straight off the backend row.
   *
   * Deliberately NOT "logged today": deciding whether a row falls on the athlete's
   * current day needs a canonical day boundary, which #191 placed on the backend
   * and which does not exist yet. Rendering the sample's own date makes no
   * timezone claim at all.
   */
  lastLoggedDate: MetricState<string>;
}

const SORE_WORDS = ["None", "Mild", "Moderate", "High"] as const;

/** Backend 0–10 soreness scalar (higher = worse) to a word. */
function sorenessWord(v: number): string {
  if (v <= 1) return SORE_WORDS[0];
  if (v <= 4) return SORE_WORDS[1];
  if (v <= 7) return SORE_WORDS[2];
  return SORE_WORDS[3];
}

/**
 * Every field on `WellnessSampleOut` is nullable except identity, so each tile
 * resolves independently: a row with HRV but no resting HR shows one value and one
 * em-dash, never a borrowed number.
 *
 * A day has MORE THAN ONE row. `WellnessSample` is keyed (user, date, source), so an
 * Oura sync and a manual check-in on the same morning are two rows, and reading only
 * the newest dropped whichever landed first — the athlete reported soreness and the
 * tile said "not recorded". Resolution here is per signal across every row for the
 * latest date, which is the shape the readiness engine already resolves to.
 *
 * Which source to BELIEVE when two report the same signal is a physiological judgement
 * (a device measures HRV better than a person; nobody measures soreness but the
 * athlete) and it is not this module's to make — `app/logic/wellness_source_authority`
 * owns it. So the resolved value is taken from `readiness.components` when the backend
 * published one for that signal, and only falls back to the day's first non-null when
 * it did not. That keeps the tile agreeing with the ring above it: both then show the
 * number the engine actually used.
 */
export function morningSection(
  resource: AuthedResource<WellnessSampleOut[]>,
  readiness: AuthedResource<ReadinessScore>,
): MorningView {
  const day = fold<WellnessSampleOut[], WellnessSampleOut[]>(resource, (rows) => {
    if (rows.length === 0) return { kind: "empty", reason: "no_data" };
    // The endpoint orders (date desc, created_at desc), so rows[0] carries the latest
    // date and the day's rows are the contiguous run sharing it.
    const latestDate = rows[0].date;
    return { kind: "value", value: rows.filter((r) => r.date === latestDate) };
  });

  // Only trust the engine's resolution when it is describing the same day these tiles
  // are showing. Components exist only for a sample the backend considered fresh, so a
  // stale day (or an athlete with no modeled state) legitimately has none.
  const resolved: Record<string, number> = {};
  if (readiness.status === "success" && day.kind === "value") {
    const score = readiness.data;
    if (score.wellness_sample?.date === day.value[0]?.date) {
      for (const c of score.components ?? []) resolved[c.signal] = c.value;
    }
  }

  const field = (
    signal: string,
    read: (s: WellnessSampleOut) => number | null | undefined,
    format: (v: number) => string,
  ): MetricState<string> => {
    if (day.kind !== "value") return day;
    const engine = resolved[signal];
    if (engine != null) return { kind: "value", value: format(engine) };
    const reported = day.value.map(read).find((v) => v != null);
    return fromNullable(reported == null ? null : format(reported), "not_recorded");
  };

  const metrics: MorningMetric[] = [
    { key: "hrv", label: "HRV", value: field("hrv_ms", (s) => s.hrv_ms, (v) => `${v} ms`) },
    { key: "sleep", label: "Sleep", value: field("sleep_hours", (s) => s.sleep_hours, (v) => `${v} h`) },
    { key: "resting_hr", label: "Rest HR", value: field("resting_hr", (s) => s.resting_hr, (v) => `${v} bpm`) },
    { key: "soreness", label: "Soreness", value: field("soreness", (s) => s.soreness, sorenessWord) },
  ];

  const lastLoggedDate: MetricState<string> =
    day.kind === "value" ? { kind: "value", value: day.value[0].date } : day;

  return { metrics, lastLoggedDate };
}

// ---- Twin snapshot -------------------------------------------------------------

export interface TwinSnapshotView {
  aerobic: MetricState<number>;
  strength: MetricState<number>;
  meanFatigue: MetricState<number>;
  peakTissue: MetricState<{ region: string; value: number }>;
}

/**
 * The newest recorded snapshot only. `snapshotCapacity` already returns null for a
 * missing axis rather than 0, so an unavailable capacity stays unavailable here.
 *
 * Reads no `snapshot_id` and no confidence field (#187: the annotation is in scope,
 * its consequences are not) — the selector behaves identically on objects that omit
 * them entirely, which is the proof that correcting the type was hygiene and not
 * covert feature expansion.
 */
export function twinSnapshotSection(
  resource: AuthedResource<StateHistorySnapshotRead[]>,
): TwinSnapshotView {
  const latest = fold<StateHistorySnapshotRead[], StateHistorySnapshotRead>(resource, (rows) =>
    rows.length === 0
      ? { kind: "empty", reason: "no_state_yet" }
      : { kind: "value", value: rows[rows.length - 1] },
  );

  const derive = <R,>(read: (s: StateHistorySnapshotRead) => R | null): MetricState<R> =>
    latest.kind === "value" ? fromNullable(read(latest.value), "not_recorded") : latest;

  return {
    aerobic: derive((s) => {
      const v = snapshotCapacity(s, "aerobic");
      return v == null ? null : Math.round(v);
    }),
    strength: derive((s) => {
      const v = snapshotCapacity(s, "max_strength");
      return v == null ? null : Math.round(v);
    }),
    meanFatigue: derive((s) => Math.round(meanFatigue(s))),
    peakTissue: derive((s) => peakTissue(s)),
  };
}

// ---- Training load + habit (GET /v1/dashboard/overview) -------------------------

export interface LoadView {
  acwr: MetricState<number>;
  status: TrainingLoadMetrics["status"];
  sweetSpot: MetricState<{ low: number; high: number }>;
}

export interface HabitView {
  pct: MetricState<number>;
  streakDays: MetricState<number>;
}

export function loadSection(resource: AuthedResource<OverviewMetrics>): LoadView {
  const load = fold(resource, (data) => ({ kind: "value", value: data.training_load }) as const);
  return {
    acwr: load.kind === "value" ? fromNullable(load.value.acwr, "insufficient_history") : load,
    // "insufficient" is the backend's own word for "no baseline yet", so an absent
    // payload lands on it rather than on an invented judgement.
    status: load.kind === "value" ? load.value.status : "insufficient",
    sweetSpot:
      load.kind === "value"
        ? { kind: "value", value: { low: load.value.sweet_spot_low, high: load.value.sweet_spot_high } }
        : load,
  };
}

/**
 * `pct` is null when nothing was scheduled. It stays empty rather than becoming 0,
 * so the progress bar draws nothing at all — a 0% bar is a claim about adherence,
 * and ADR-0046 is explicit that `0` never encodes "unknown".
 */
export function habitSection(resource: AuthedResource<OverviewMetrics>): HabitView {
  const adherence = fold(resource, (data) => ({ kind: "value", value: data.adherence }) as const);
  return {
    pct: adherence.kind === "value" ? fromNullable(adherence.value.pct, "no_data") : adherence,
    streakDays:
      adherence.kind === "value"
        ? { kind: "value", value: adherence.value.streak_days }
        : adherence,
  };
}

// ---- Primary session action (map #182, L3 / #188) -------------------------------

/**
 * Whether a real, playable session can be constructed from canonical prescription
 * data. Ticket #183 established it cannot: there are no per-phase duration/zone/
 * pace/rest fields on `WorkoutPrescription`. (Since P1 `/planning/today` serves an
 * immutable revision, so a session now has a revision identity; the missing per-phase
 * timeline alone keeps this false.)
 *
 * #188 ruled this must be a NAMED CONSTANT, never a predicate over prescription
 * data — a predicate would quietly start returning true the moment some unrelated
 * field became non-empty, and the athlete would be handed a simulated workout.
 *
 * Flip this only when a per-phase timeline AND a revision identity both exist.
 */
export const PLAYABLE_SESSION_AVAILABLE = false;

export interface PrimaryActionView {
  /** True only when an authenticated athlete could start a real session. */
  canStartSession: boolean;
  /** Why not, for the surface that would otherwise show the button. */
  unavailableReason: string | null;
}

export function primaryActionSection(): PrimaryActionView {
  return PLAYABLE_SESSION_AVAILABLE
    ? { canStartSession: true, unavailableReason: null }
    : {
        canStartSession: false,
        unavailableReason: "Guided sessions aren't available yet — your plan is on the Planning screen.",
      };
}
