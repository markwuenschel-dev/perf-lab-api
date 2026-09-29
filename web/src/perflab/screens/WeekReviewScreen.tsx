// src/perflab/screens/WeekReviewScreen.tsx
//
// Week review — one block week closed out: what was planned, what happened, what
// moved in the twin, and what is already determined for the week after.
//
// Everything a signed-in athlete sees comes from GET /v1/planning/week-review. The
// honesty rules this screen must not bend:
//
//   - An axis whose confidence is `insufficient` is "not measured": its value is an
//     unrefined prior, so it gets neither a value nor a delta.
//   - "What changes next week" lists facts the backend has already determined (block
//     deload/benchmark flags, the block ending, the prescriber's adherence bias, active
//     plan-revision triggers). An empty list means no such fact applies — NOT that a
//     model reviewed next week and found nothing to change, and the copy must not say so.
//   - There is no accept/override backend, so there are no Accept / Override buttons.
//   - Feedback for an unreviewed session goes through the existing FeedbackModal
//     (POST /v1/feedback); this screen invents no feedback endpoint and no RPE judgement.
//
// Guests get a clearly labelled sample week rendered through the same body.
import { useState, type ReactNode } from "react";
import * as api from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import { cn } from "@/lib/utils";
import type { WeekReview, WeekReviewAxisMove, WeekReviewNextItem, WeekReviewSession } from "@/types";
import { usePerfLab } from "../store";
import { useAuthedResource } from "../useAuthedResource";
import { ResourceState } from "../ResourceState";
import { resourceData } from "../resource";
import { Card, Pill, ScreenHeader, SectionLabel } from "../ui";
import { SampleTag } from "../SampleTag";
import { axisLabel } from "../prescription/axes";

// Console section label (mono 10px, faint) — a local override of the shared
// SectionLabel default, which the not-yet-migrated screens still render at 11px.
const LABEL = "text-[10px] text-faint";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** "YYYY-MM-DD" as a LOCAL date — `new Date(iso)` would read it as UTC midnight. */
function parseIsoLocal(iso: string): Date | null {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d);
}

/** "13–19 Sep", or "29 Sep – 5 Oct" across a month boundary. */
function fmtWindow(startIso: string, endIso: string): string {
  const s = parseIsoLocal(startIso);
  const e = parseIsoLocal(endIso);
  if (!s || !e) return `${startIso} – ${endIso}`;
  return s.getMonth() === e.getMonth()
    ? `${s.getDate()}–${e.getDate()} ${MONTHS[e.getMonth()]}`
    : `${s.getDate()} ${MONTHS[s.getMonth()]} – ${e.getDate()} ${MONTHS[e.getMonth()]}`;
}

const dayOf = (iso: string): string => {
  const d = parseIsoLocal(iso);
  return d ? DOW[d.getDay()] : iso;
};

const titleCase = (s: string): string => (s ? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ") : s);

/** Signed, one decimal, with a real minus sign. Zero reads "0.0". */
function signed(v: number): string {
  const r = Math.round(v * 10) / 10;
  if (r === 0) return "0.0";
  return `${r > 0 ? "+" : "−"}${Math.abs(r).toFixed(1)}`;
}

/** Aerobic lives on a 0–650 scale, every other axis on 0–100 — integers read fine
 *  for the big one, one decimal keeps the small movements visible on the rest. */
const fmtAxisValue = (v: number): string => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1));

const fmtRpe = (v: number): string => (Number.isInteger(v) ? String(v) : v.toFixed(1));

// ──────────────────────────────────────────────────────────────────────────
// Screen shell
// ──────────────────────────────────────────────────────────────────────────

export function WeekReviewScreen() {
  const { token } = useAuth();
  return token ? <AuthedWeekReview /> : <GuestWeekReview />;
}

function Shell({ badge, children }: { badge?: ReactNode; children: ReactNode }) {
  const { actions } = usePerfLab();
  const { token } = useAuth();
  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader
        title="Week review"
        badge={badge}
        subtitle="What you planned, what you did, what moved in the twin — and what is already set for next week."
      >
        {/* Check-in writes wellness: signed-in athletes only, as on the other console screens. */}
        {token != null && (
          <button onClick={actions.openCheckin} className="rounded-[9px] border border-white/[0.07] bg-white/[0.04] px-[14px] py-[9px] text-[12.5px] font-semibold leading-none text-soft">
            Check in
          </button>
        )}
        <button onClick={actions.openLog} className="rounded-[9px] bg-ac px-[15px] py-[9px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Log workout</button>
      </ScreenHeader>
      {children}
    </section>
  );
}

function WindowChip({ review }: { review: WeekReview }) {
  const w = review.window;
  if (!w) return null;
  return <Pill>{`block wk ${w.week_number} · ${fmtWindow(w.start, w.end)}`}</Pill>;
}

// ──────────────────────────────────────────────────────────────────────────
// Authenticated: live-only.
// ──────────────────────────────────────────────────────────────────────────

interface WeekSel {
  block_id: number;
  week_number: number;
}

function AuthedWeekReview() {
  const { state, actions } = usePerfLab();
  // null = let the backend pick the current block's current week.
  const [sel, setSel] = useState<WeekSel | null>(null);

  // `feedbackRefreshKey` is bumped after FeedbackModal records an outcome, so a
  // session reviewed from this screen re-reads as reviewed.
  const resource = useAuthedResource<WeekReview>(
    (t) => api.getWeekReview(t, sel ?? undefined),
    [sel?.block_id, sel?.week_number, state.feedbackRefreshKey],
  );
  const data = resourceData(resource);

  return (
    <Shell badge={data ? <WindowChip review={data} /> : undefined}>
      <ResourceState
        resource={resource}
        variant="box"
        guest={{ title: "Sign in to review your week" }}
        empty={{ title: "Nothing to review" }}
        loadingContent={{ title: "Loading your week…", body: "Reading this block week's sessions and state." }}
        error={{ title: "Couldn't load your week review" }}
        staleLabel="Couldn't refresh this week — showing the last one loaded."
      >
        {(review) =>
          review.available ? (
            <ReviewBody
              review={review}
              stepper={
                review.window ? (
                  <WeekStepper
                    week={review.window.week_number}
                    of={review.window.duration_weeks}
                    isCurrent={review.window.is_current_week}
                    onGo={(week_number) => setSel({ block_id: review.window!.block_id, week_number })}
                    onCurrent={sel != null ? () => setSel(null) : undefined}
                  />
                ) : null
              }
              onFeedback={(id) => actions.openFeedback(id)}
            />
          ) : (
            <UnavailableNotice reason={review.reason ?? null} onPlanning={() => actions.setScreen("planning")} />
          )
        }
      </ResourceState>
    </Shell>
  );
}

/** Honest copy for each `available: false` reason. Unknown reasons (version skew)
 *  degrade to a generic line rather than a guess. */
const UNAVAILABLE_COPY: Record<string, { title: string; body: string }> = {
  no_active_block: {
    title: "No active training block",
    body: "A week review reads one week of a training block, and you don't have an active block. Create one in Planning and its weeks will close out here.",
  },
  no_state: {
    title: "No twin state to review against",
    body: "There's no modeled state for your account yet, so nothing about this week can be measured. Complete onboarding or log a workout to seed your twin.",
  },
  state_invalid: {
    title: "Your twin state couldn't be read",
    body: "Your latest stored state failed validation, so this review shows nothing derived from it rather than numbers it can't stand behind. Your logged sessions are unaffected.",
  },
};

function UnavailableNotice({ reason, onPlanning }: { reason: string | null; onPlanning: () => void }) {
  const copy = (reason && UNAVAILABLE_COPY[reason]) || {
    title: "Week review isn't available",
    body: "The server couldn't build a review for this week.",
  };
  return (
    <div role="status" className="flex min-h-[240px] flex-col items-center justify-center gap-3 rounded-[18px] border border-dashed border-white/10 p-[30px] text-center text-mute">
      <div className="text-[15px] font-bold leading-[1.3] text-ink">{copy.title}</div>
      <div className="max-w-[400px] text-[12.5px] font-medium leading-[1.5]">{copy.body}</div>
      {reason === "no_active_block" && (
        <button onClick={onPlanning} className="mt-[6px] rounded-[9px] border border-white/[0.07] bg-white/[0.04] px-4 py-[10px] text-[12.5px] font-semibold leading-none text-soft">
          Go to Planning
        </button>
      )}
    </div>
  );
}

function WeekStepper({ week, of, isCurrent, onGo, onCurrent }: {
  week: number;
  of: number;
  isCurrent: boolean;
  onGo: (week: number) => void;
  onCurrent?: () => void;
}) {
  const btn = "grid h-[28px] w-[28px] place-items-center rounded-[7px] border border-white/[0.07] bg-white/[0.04] text-[13px] leading-none text-soft disabled:opacity-40";
  return (
    <div className="flex items-center gap-2">
      <button type="button" aria-label="Previous week" className={btn} disabled={week <= 1} onClick={() => onGo(week - 1)}>‹</button>
      <span className="min-w-[92px] text-center font-mono text-[11px] font-semibold leading-none text-mute">
        wk {week} / {of}{isCurrent ? " · now" : ""}
      </span>
      <button type="button" aria-label="Next week" className={btn} disabled={week >= of} onClick={() => onGo(week + 1)}>›</button>
      {onCurrent && (
        <button type="button" onClick={onCurrent} className="rounded-[7px] border border-white/[0.07] bg-white/[0.04] px-[10px] py-[8px] text-[11px] font-semibold leading-none text-soft">
          Current week
        </button>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Body — shared by the live review and the guest sample.
// ──────────────────────────────────────────────────────────────────────────

function ReviewBody({ review, stepper, onFeedback, sample = false }: {
  review: WeekReview;
  stepper?: ReactNode;
  /** Absent for the guest sample: its sessions are not real, so nothing can be reported. */
  onFeedback?: (plannedSessionId: number) => void;
  sample?: boolean;
}) {
  return (
    <div className="flex flex-col gap-[14px]">
      {(stepper || sample) && (
        <div className="flex items-center justify-between gap-3">
          {sample ? <SampleTag /> : <span />}
          {stepper}
        </div>
      )}
      <StatTiles review={review} />
      <div className="grid grid-cols-1 gap-[14px] lg:grid-cols-2">
        <WhatMovedCard review={review} sample={sample} />
        <HowItFeltCard sessions={review.sessions ?? []} onFeedback={onFeedback} sample={sample} />
      </div>
      <NextWeekCard items={review.next_week ?? []} status={review.next_week_status ?? null} sample={sample} />
    </div>
  );
}

// ---- Stat tiles ---------------------------------------------------------------

function Stat({ label, value, sub, valueClass = "text-ink" }: { label: string; value: string; sub: string; valueClass?: string }) {
  return (
    <div data-tile="1" className="rounded-[14px] border border-white/[0.07] bg-tile p-4">
      <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">{label}</div>
      <div className={cn("mt-[11px] font-mono text-[26px] font-semibold leading-none", valueClass)}>{value}</div>
      <div className="mt-2 text-[11px] font-medium leading-[1.3] text-mute">{sub}</div>
    </div>
  );
}

function StatTiles({ review }: { review: WeekReview }) {
  const c = review.counts;
  const m = review.moved;
  const wk = review.window?.week_number;

  const loggedSub = c
    ? [`${c.skipped} skipped`, `${c.pending} pending`, ...(c.modified > 0 ? [`${c.modified} modified`] : [])].join(" · ")
    : "";

  const adherence = c?.adherence_pct;
  const adherenceSub = c ? (c.due === 0 ? "nothing due yet this week" : `${c.completed} of ${c.due} due so far`) : "";

  // Mean fatigue at week end, with the week's own movement and the prior week's for
  // comparison — each only when both of its bracketing snapshots exist.
  const fEnd = m?.mean_fatigue_end ?? null;
  const fStart = m?.mean_fatigue_start ?? null;
  const fPrev = m?.mean_fatigue_previous_week_start ?? null;
  const fatigueParts: string[] = [];
  if (fEnd != null && fStart != null) fatigueParts.push(`${signed(fEnd - fStart)} this week`);
  if (fStart != null && fPrev != null) fatigueParts.push(`${signed(fStart - fPrev)} prior week`);
  const fatigueSub = fEnd == null ? "no state snapshot by week end" : fatigueParts.join(" · ") || "no week-start snapshot to compare";

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Stat label="Planned" value={c ? String(c.planned) : "—"} sub={wk != null ? `sessions in block wk ${wk}` : "sessions this week"} />
      <Stat label="Completed" value={c ? String(c.completed) : "—"} sub={loggedSub} valueClass={c && c.completed > 0 ? "text-good" : "text-ink"} />
      <Stat label="Adherence" value={adherence != null ? `${Math.round(adherence)}%` : "—"} sub={adherenceSub} valueClass={adherence != null ? "text-ink" : "text-dim"} />
      <Stat label="Mean fatigue" value={fEnd != null ? fEnd.toFixed(0) : "—"} sub={fatigueSub} valueClass={fEnd != null ? "text-ink" : "text-dim"} />
    </div>
  );
}

// ---- What moved ---------------------------------------------------------------

function deltaClass(d: number): string {
  const r = Math.round(d * 10) / 10;
  return r > 0 ? "text-good" : r < 0 ? "text-hot" : "text-dim";
}

function MovedRow({ move }: { move: WeekReviewAxisMove }) {
  const label = axisLabel(move.axis);
  if (!move.measured || move.end == null) {
    // An unrefined prior is not a measurement: no value, no delta — ever.
    return (
      <div data-testid={`moved-${move.axis}`} className="flex items-center gap-[10px]">
        <span className="flex-1 text-[12px] font-medium leading-none text-mute">{label}</span>
        <span className="font-mono text-[10.5px] font-semibold uppercase leading-none tracking-[0.08em] text-dim">not measured</span>
      </div>
    );
  }
  return (
    <div data-testid={`moved-${move.axis}`} className="flex items-center gap-[10px]">
      <span className="flex-1 text-[12px] font-medium leading-none text-mute">{label}</span>
      <span className="font-mono text-[12px] font-semibold leading-none text-soft">{fmtAxisValue(move.end)}</span>
      {move.delta != null ? (
        <span className={cn("w-[52px] text-right font-mono text-[12px] font-semibold leading-none", deltaClass(move.delta))}>{signed(move.delta)}</span>
      ) : (
        <span title="Not measured at week start, so there is nothing to compare against." className="w-[52px] text-right font-mono text-[12px] font-semibold leading-none text-dim">—</span>
      )}
    </div>
  );
}

function WhatMovedCard({ review, sample }: { review: WeekReview; sample: boolean }) {
  const axes = review.moved?.capacity ?? [];
  // Measured axes first — they are the ones with something to say.
  const ordered = [...axes.filter((a) => a.measured && a.end != null), ...axes.filter((a) => !(a.measured && a.end != null))];
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <SectionLabel className={LABEL}>What moved</SectionLabel>
        {sample && <SampleTag />}
      </div>
      {ordered.length === 0 ? (
        <p className="mt-4 text-[12px] font-medium leading-[1.5] text-mute">
          No state snapshot falls at or before this week's end, so there is nothing to compare.
        </p>
      ) : (
        <div className="mt-4 flex flex-col gap-[13px]">
          {ordered.map((a) => (
            <MovedRow key={a.axis} move={a} />
          ))}
        </div>
      )}
      <div className="mt-4 border-t border-white/[0.07] pt-3 text-[11px] font-medium leading-[1.5] text-mute">
        Deltas run from the latest snapshot at the week's start to the latest at its end. Axes still at their
        unrefined starting estimate are not measured and get no delta.
      </div>
    </Card>
  );
}

// ---- How it felt --------------------------------------------------------------

/** Felt vs prescribed, compared exactly — no tolerance band this screen would be inventing. */
function feltComparison(felt: number, prescribed: number): { text: string; cls: string } {
  if (felt > prescribed) return { text: "above prescribed", cls: "border-hot/[0.35] bg-hot/[0.1] text-hot" };
  if (felt < prescribed) return { text: "below prescribed", cls: "border-teal/[0.3] bg-teal/[0.1] text-teal" };
  return { text: "as prescribed", cls: "border-good/[0.3] bg-good/[0.1] text-good" };
}

function feedbackNote(s: WeekReviewSession): string | null {
  if (s.feedback_status == null) return null;
  if (s.modified || s.feedback_status === "modified") {
    const dims = [
      s.modified_volume && "volume",
      s.modified_intensity && "intensity",
      s.modified_exercises && "exercises",
    ].filter(Boolean);
    const what = dims.length ? `modified ${dims.join(", ")}` : "modified";
    return s.modification_reason ? `${what} — ${s.modification_reason}` : what;
  }
  if (s.followed_as_prescribed) return "followed as prescribed";
  return `reported ${s.feedback_status}`;
}

const chip = "rounded-[7px] border px-[9px] py-[6px] text-[11px] font-semibold leading-none";

function FeltRow({ s, onFeedback }: { s: WeekReviewSession; onFeedback?: (id: number) => void }) {
  const kind = titleCase(s.category || s.modality);
  const mode = s.modality && s.modality !== s.category ? ` · ${titleCase(s.modality)}` : "";
  const name = `${kind}${mode} (${dayOf(s.scheduled_date)})`;
  const meta = s.prescribed_rpe != null ? `prescribed RPE ${fmtRpe(s.prescribed_rpe)}` : "no RPE prescribed";
  const terminal = s.status === "completed" || s.status === "skipped";
  const note = feedbackNote(s);

  let body: ReactNode;
  if (s.status === "skipped") {
    body = <span className={cn(chip, "border-white/[0.07] bg-white/[0.04] text-mute")}>Skipped</span>;
  } else if (s.status === "completed") {
    if (s.felt_rpe == null) {
      body = <span className="text-[11px] font-medium leading-none text-dim">completed · no session RPE logged</span>;
    } else if (s.prescribed_rpe == null) {
      body = <span className={cn(chip, "border-white/[0.07] bg-white/[0.04] text-soft")}>felt RPE {fmtRpe(s.felt_rpe)}</span>;
    } else {
      const cmp = feltComparison(s.felt_rpe, s.prescribed_rpe);
      body = (
        <>
          <span className={cn(chip, cmp.cls)}>felt RPE {fmtRpe(s.felt_rpe)}</span>
          <span className="text-[11px] font-medium leading-none text-dim">{cmp.text}</span>
        </>
      );
    }
  } else {
    body = <span className="text-[11px] font-medium leading-none text-dim">{s.status === "rescheduled" ? "rescheduled" : "not done yet"}</span>;
  }

  return (
    <div data-testid={`felt-${s.planned_session_id}`} className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-[10px]">
        <span className="text-[12.5px] font-semibold leading-none text-ink">{name}</span>
        <span className="font-mono text-[10.5px] leading-none text-faint">{meta}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {body}
        {note && <span className="text-[11px] font-medium leading-none text-dim">· {note}</span>}
        {/* Feedback describes an outcome, so only completed/skipped sessions can take
            it (ADR-0070), and only once. It goes through the existing FeedbackModal. */}
        {terminal && note == null && onFeedback && (
          <button
            type="button"
            onClick={() => onFeedback(s.planned_session_id)}
            className="ml-auto rounded-[8px] border border-white/[0.07] bg-white/[0.04] px-[10px] py-[7px] text-[11px] font-semibold leading-none text-soft"
          >
            Add feedback
          </button>
        )}
      </div>
    </div>
  );
}

function HowItFeltCard({ sessions, onFeedback, sample }: {
  sessions: WeekReviewSession[];
  onFeedback?: (id: number) => void;
  sample: boolean;
}) {
  const ordered = [...sessions].sort((a, b) => a.scheduled_date.localeCompare(b.scheduled_date));
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <SectionLabel className={LABEL}>How it felt</SectionLabel>
        {sample && <SampleTag />}
      </div>
      {ordered.length === 0 ? (
        <p className="mt-4 text-[12px] font-medium leading-[1.5] text-mute">No sessions are scheduled in this week.</p>
      ) : (
        <div className="mt-4 flex flex-col gap-[14px]">
          {ordered.map((s) => (
            <FeltRow key={s.planned_session_id} s={s} onFeedback={onFeedback} />
          ))}
        </div>
      )}
    </Card>
  );
}

// ---- What changes next week ---------------------------------------------------

const KIND_DOT: Record<WeekReviewNextItem["kind"], string> = {
  plan: "var(--color-info)",
  safety: "var(--color-hot)",
  assess: "var(--color-cat-5)",
};

/** The empty-list copy. Deliberately does not claim anything reviewed next week. */
const NOTHING_SCHEDULED_COPY =
  "Nothing already scheduled changes next week — no deload, benchmark, block end, adherence bias or active safety trigger applies. Sessions are still prescribed on the day against your state.";

function NextWeekCard({ items, status, sample }: {
  items: WeekReviewNextItem[];
  status: WeekReview["next_week_status"];
  sample: boolean;
}) {
  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <SectionLabel className={LABEL}>What changes next week</SectionLabel>
        {sample ? <SampleTag /> : <span className="text-[11px] font-medium leading-none text-dim">already determined — sessions are still set on the day</span>}
      </div>
      {items.length === 0 ? (
        <p data-testid="next-week-empty" className="text-[12px] font-medium leading-[1.5] text-mute">
          {status === "nothing_scheduled_to_change" || status == null ? NOTHING_SCHEDULED_COPY : "No changes listed."}
        </p>
      ) : (
        <div className="flex flex-col">
          {items.map((c, i) => (
            <div key={`${c.source}-${i}`} data-testid="next-week-item" className="flex items-start gap-3 border-b border-white/[0.07] py-3 last:border-b-0">
              <span className="mt-[3px] h-[9px] w-[9px] flex-none rounded-full" style={{ background: KIND_DOT[c.kind] ?? "var(--color-faint)" }} />
              <div className="flex-1">
                <div className="text-[13px] font-semibold leading-none text-ink">{c.title}</div>
                <div className="mt-[6px] text-[11.5px] font-medium leading-[1.5] text-mute">{c.reason}</div>
              </div>
              <span className="flex-none rounded-[6px] border border-white/[0.07] px-2 py-[5px] font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.08em] text-faint">{c.kind}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Guest: a labelled sample week, never shown to a signed-in athlete.
// ──────────────────────────────────────────────────────────────────────────

const SAMPLE_REVIEW: WeekReview = {
  available: true,
  window: { block_id: 0, week_number: 3, duration_weeks: 8, start: "2026-09-14", end: "2026-09-20", is_current_week: false },
  counts: { planned: 4, completed: 3, skipped: 1, modified: 0, pending: 0, due: 4, adherence_pct: 75 },
  moved: {
    mean_fatigue_previous_week_start: 33,
    mean_fatigue_start: 35,
    mean_fatigue_end: 31,
    capacity: [
      { axis: "max_strength", measured: true, status_start: "established", status_end: "established", start: 71.6, end: 73, delta: 1.4 },
      { axis: "work_capacity", measured: true, status_start: "provisional", status_end: "provisional", start: 69.1, end: 70, delta: 0.9 },
      { axis: "aerobic", measured: true, status_start: "established", status_end: "established", start: 420, end: 426, delta: 6 },
      { axis: "glycolytic", measured: false, status_start: "insufficient", status_end: "insufficient" },
      { axis: "power", measured: false, status_start: "insufficient", status_end: "insufficient" },
    ],
  },
  sessions: [
    { planned_session_id: -1, scheduled_date: "2026-09-14", week_number: 3, category: "recovery", modality: "run", status: "skipped", is_deload: false, is_benchmark: false, prescribed_rpe: 4, modified: false },
    { planned_session_id: -2, scheduled_date: "2026-09-15", week_number: 3, category: "strength", modality: "lower", status: "completed", is_deload: false, is_benchmark: false, felt_rpe: 9, prescribed_rpe: 7.5, modified: false },
    { planned_session_id: -3, scheduled_date: "2026-09-18", week_number: 3, category: "endurance", modality: "run", status: "completed", is_deload: false, is_benchmark: false, felt_rpe: 4, prescribed_rpe: 5, feedback_status: "completed", followed_as_prescribed: true, modified: false },
  ],
  next_week: [
    { kind: "safety", source: "trigger:tissue_t.knee", title: "Knee tissue load high", reason: "tissue_t.knee is 46 (threshold 40); the prescriber adjusts sessions for this until it falls below the threshold." },
    { kind: "plan", source: "block:deload_week", title: "Deload week", reason: "Week 4 is a scheduled deload (every 4 weeks); session volume is scaled by 0.6." },
    { kind: "assess", source: "block:benchmark_session", title: "Benchmark session on 2026-09-24", reason: "The block schedules a periodic retest in this week." },
  ],
  next_week_status: "changes_listed",
};

function GuestWeekReview() {
  const { actions } = usePerfLab();
  return (
    <Shell badge={<WindowChip review={SAMPLE_REVIEW} />}>
      <div className="flex items-center gap-2 rounded-[12px] border border-mint/25 bg-mint/[0.08] px-4 py-[10px] text-[12px] font-medium leading-none text-teal">
        <span className="h-[7px] w-[7px] flex-none rounded-full bg-ac" />
        <span className="flex-1">Preview — sample athlete. Sign in to review your own block weeks.</span>
        <button onClick={actions.openAuth} className="rounded-[7px] border border-white/[0.07] bg-white/[0.04] px-[10px] py-[6px] text-[11px] font-semibold leading-none text-soft">
          Sign in
        </button>
      </div>
      <ReviewBody review={SAMPLE_REVIEW} sample />
    </Shell>
  );
}
