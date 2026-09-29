// src/perflab/macrocycles.ts
//
// Non-component helpers for the Macrocycle (program) feature, kept out of the
// screen/overlay files so exporting them doesn't trip
// react-refresh/only-export-components. Shared by OverviewScreen (the real
// "week X of Y" header) and ObjectivesScreen (the Program section).
import type { MacrocycleBlockSummary, MacrocycleRead, WeekProgress } from "@/types";

/** The athlete's current program: the first active macrocycle, else the first
 *  one returned (the list endpoint defaults to active). null when there is none. */
export function activeMacrocycle(macros: MacrocycleRead[] | null): MacrocycleRead | null {
  if (!macros || macros.length === 0) return null;
  return macros.find((m) => m.status === "active") ?? macros[0];
}

/**
 * Human "week X of Y" from a WeekProgress. For an open horizon (the anchor
 * objective has no target_date, so total_weeks is null) we drop the "of Y" and
 * show just "week N" rather than inventing a finish line.
 */
export function weekProgressLabel(wp: WeekProgress): string {
  if (wp.total_weeks != null) return `week ${wp.current_week} of ${wp.total_weeks}`;
  return `week ${wp.current_week}`;
}

/** One segment of the program timeline strip. Its width is ∝ `weeks`. */
export interface TimelineSegment {
  key: string;
  kind: "block" | "unplanned";
  label: string;
  meta: string;
  weeks: number;
  /** Date-derived block phase from the API; null for the unplanned remainder. */
  phase: MacrocycleBlockSummary["phase"] | null;
  /** Block-local final-week taper (1-based week within the block), not an event taper. */
  taperWeek: number | null;
  /** 1-based block-local weeks whose stored sessions are flagged benchmark. */
  benchmarkWeeks: number[];
}

export interface ProgramTimeline {
  segments: TimelineSegment[];
  totalWeeks: number;
  /** Position of today along the strip as a 0–100 percentage; null without a current block. */
  nowPct: number | null;
}

const DAY_MS = 86_400_000;

function isoDayNumber(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y, m - 1, d) / DAY_MS;
}

function localDayNumber(today: Date): number {
  return Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()) / DAY_MS;
}

/**
 * The program timeline: only blocks that exist (blocks are generated one at a time and
 * never persisted ahead, ADR-0040), in API order, then the uncovered remainder up to the
 * anchor's target date (`unplanned_weeks`) as one "unplanned" segment — never invented
 * future blocks. The NOW pin sits inside the block whose API phase is "current".
 */
export function programTimeline(m: MacrocycleRead, today: Date = new Date()): ProgramTimeline {
  const todayN = localDayNumber(today);
  let nowPct: number | null = null;
  let before = 0;
  let nowWeeks: number | null = null;

  const segments: TimelineSegment[] = m.blocks.map((b) => {
    const weeks = Math.max(1, b.duration_weeks);
    let meta: string;
    if (b.phase === "current") {
      const daysInto = Math.min(Math.max(todayN - isoDayNumber(b.start_date), 0), weeks * 7);
      const blockWeek = Math.min(weeks, Math.floor(daysInto / 7) + 1);
      nowWeeks = before + daysInto / 7;
      meta = `wk ${blockWeek} of ${weeks} · now`;
    } else if (b.phase === "completed") {
      meta = `${weeks} wk · complete`;
    } else {
      meta = `${weeks} wk`;
    }
    before += weeks;
    return {
      key: `block-${b.id}`,
      kind: "block",
      label: b.goal,
      meta,
      weeks,
      phase: b.phase,
      taperWeek: b.block_taper_week,
      benchmarkWeeks: b.benchmark_weeks,
    };
  });

  if (m.unplanned_weeks != null && m.unplanned_weeks > 0) {
    segments.push({
      key: "unplanned",
      kind: "unplanned",
      label: "Unplanned",
      meta: `${m.unplanned_weeks} wk · not generated yet`,
      weeks: m.unplanned_weeks,
      phase: null,
      taperWeek: null,
      benchmarkWeeks: [],
    });
  }

  const totalWeeks = segments.reduce((s, x) => s + x.weeks, 0);
  if (nowWeeks != null && totalWeeks > 0) nowPct = Math.min(100, Math.max(0, (nowWeeks / totalWeeks) * 100));
  return { segments, totalWeeks, nowPct };
}
