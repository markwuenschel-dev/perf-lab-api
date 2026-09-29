// src/perflab/screens/ObjectivesScreen.tsx
//
// Objectives — program timeline (console redesign PR 6). A multi-domain goal — a
// race, a meet, a Hyrox, a benchmark PR — and the program built toward it.
//
//   - Program hero: the active macrocycle's blocks as a strip whose widths are ∝
//     weeks, followed by the unplanned remainder up to the target date. Only blocks
//     that exist are drawn — future blocks are never persisted (ADR-0040) — and
//     "taper" is the block-local final week, not an event taper.
//   - Display order: drag (or move up/down) writes `display_rank` via
//     PUT /v1/objectives/order. It is display only — it never touches `priority` and
//     never changes what drives training. The PRIMARY badge marks the objective
//     GET /v1/objectives/driving names, not whatever sits at rank 1.
//   - No "this week" line per objective: there is no session→objective attribution
//     to back it, so it is hidden rather than written.
import { useState, type DragEvent } from "react";
import * as api from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import { cn } from "@/lib/utils";
import type { ApiError, AssessmentSurfaceRead, DrivingObjectiveRead, MacrocycleRead, ObjectiveRead } from "@/types";
import { usePerfLab } from "../store";
import { useAuthedResource } from "../useAuthedResource";
import { ResourceState } from "../ResourceState";
import { resourceData } from "../resource";
import {
  activeInDisplayOrder,
  closedObjectives,
  evidenceByBenchmarkCode,
  moveId,
  type EvidenceStatus,
} from "../objectives";
import { activeMacrocycle, programTimeline, type TimelineSegment } from "../macrocycles";
import { domainLabel } from "../domains";
import { Card, Pill, ScreenHeader, SectionLabel, Track } from "../ui";
import { Legend, type LegendItem } from "../viz";

// Section labels in the redesign are 10px/faint (README: Geist Mono 10px/600) — the
// SectionLabel default, which the not-yet-migrated screens still render at 11px.
const LABEL = "text-[10px] text-faint";

function statusLabel(status: ObjectiveRead["status"]): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function ObjectivesScreen() {
  const { state, actions } = usePerfLab();

  const objectivesRes = useAuthedResource<ObjectiveRead[]>(
    (t) => api.listObjectives(t),
    [state.objectivesRefreshKey],
  );

  // Every non-success branch — guest gate, first load, load failure, empty —
  // belongs to ResourceState, in that fixed order, so a failed fetch can't read as
  // "no objectives yet" and the guest gate can't be forgotten.
  return (
    <ResourceState
      resource={objectivesRes}
      isEmpty={(rows) => rows.length === 0}
      variant="screen"
      icon={<TargetIcon />}
      guest={{
        title: "Sign in to set your objectives",
        body: "Objectives — a race, a meet, a Hyrox, a benchmark PR — live on your account so your plan can point at them.",
        action: { label: "Sign in →", onClick: actions.openAuth },
      }}
      loadingContent={{ title: "Loading your objectives…", body: "Fetching what your plan is pointed at." }}
      error={{
        title: "Couldn't load your objectives",
        action: { label: "Retry", onClick: actions.refreshObjectives },
      }}
      empty={{
        title: "Set your first objective",
        body: "A race, a meet, a Hyrox, a lift PR — give your plan something to point at, benchmark-linked or free-text.",
        action: { label: "New objective →", onClick: actions.openObjectiveCreate, primary: true },
      }}
      staleLabel="Couldn't refresh your objectives — showing your last loaded list."
    >
      {(objectives) => <ObjectivesBody objectives={objectives} />}
    </ResourceState>
  );
}

function ObjectivesBody({ objectives }: { objectives: ObjectiveRead[] }) {
  const { state, actions } = usePerfLab();
  const auth = useAuth();
  const token = auth.token;

  const [mutatingId, setMutatingId] = useState<number | null>(null);
  const [mutateError, setMutateError] = useState<string | null>(null);
  // Optimistic display order. It is pinned to the objectives payload it was made
  // against, so the moment a refetch lands the server's order takes over again.
  const [override, setOverride] = useState<{ base: ObjectiveRead[]; ids: number[] } | null>(null);
  const [saving, setSaving] = useState(false);
  const [dragId, setDragId] = useState<number | null>(null);
  const [overId, setOverId] = useState<number | null>(null);
  const [announce, setAnnounce] = useState("");

  const drivingRes = useAuthedResource<DrivingObjectiveRead>(
    (t) => api.getDrivingObjective(t),
    [state.objectivesRefreshKey, state.macrocyclesRefreshKey],
  );
  const surfaceRes = useAuthedResource<AssessmentSurfaceRead>(
    (t) => api.getAssessmentSurface(t),
    [state.objectivesRefreshKey],
  );
  const driving = resourceData(drivingRes);
  const evidence = evidenceByBenchmarkCode(resourceData(surfaceRes));

  const byId = new Map(objectives.map((o) => [o.id, o]));
  const serverIds = activeInDisplayOrder(objectives).map((o) => o.id);
  const liveOverride = override && override.base === objectives ? override : null;
  const activeIds = liveOverride ? liveOverride.ids : serverIds;
  const active = activeIds.map((id) => byId.get(id)).filter((o): o is ObjectiveRead => o != null);
  const closed = closedObjectives(objectives);

  async function commitOrder(next: number[], movedId: number) {
    if (!token || saving) return;
    if (next.length === activeIds.length && next.every((id, i) => id === activeIds[i])) return;
    const previous = liveOverride;
    setOverride({ base: objectives, ids: next });
    setSaving(true);
    setMutateError(null);
    setAnnounce(`${byId.get(movedId)?.label ?? "Objective"} moved to position ${next.indexOf(movedId) + 1}.`);
    try {
      // Display order only: the full active id list, never a priority write.
      await api.setObjectiveOrder(next, token);
      actions.refreshObjectives();
    } catch (e) {
      // Roll back to the last order the server accepted, then refetch: a 400 means
      // the active set changed underneath us (created/closed elsewhere).
      setOverride(previous);
      setMutateError((e as ApiError)?.message ?? "Couldn't save that order — your previous order is back.");
      setAnnounce("Reorder failed; the previous order was restored.");
      actions.refreshObjectives();
    } finally {
      setSaving(false);
    }
  }

  function move(id: number, delta: -1 | 1) {
    const from = activeIds.indexOf(id);
    if (from < 0) return;
    void commitOrder(moveId(activeIds, from, from + delta), id);
  }

  function dropOn(targetId: number) {
    const moved = dragId;
    setDragId(null);
    setOverId(null);
    if (moved == null) return;
    const from = activeIds.indexOf(moved);
    const to = activeIds.indexOf(targetId);
    if (from < 0 || to < 0) return;
    void commitOrder(moveId(activeIds, from, to), moved);
  }

  async function markAchieved(id: number) {
    if (!token) return;
    setMutatingId(id);
    setMutateError(null);
    try {
      await api.updateObjective(id, { status: "achieved" }, token);
      actions.refreshObjectives();
    } catch (e) {
      setMutateError((e as ApiError)?.message ?? "Couldn't update that objective.");
    } finally {
      setMutatingId(null);
    }
  }

  async function remove(id: number) {
    if (!token) return;
    setMutatingId(id);
    setMutateError(null);
    try {
      await api.deleteObjective(id, token);
      actions.refreshObjectives();
    } catch (e) {
      setMutateError((e as ApiError)?.message ?? "Couldn't delete that objective.");
    } finally {
      setMutatingId(null);
    }
  }

  const drivingId = driving?.objective_id ?? null;
  const drivingWhy =
    driving?.source === "macrocycle_anchor"
      ? "PRIMARY is the objective your program is anchored to — it drives what gets prescribed."
      : driving?.source === "priority"
        ? "PRIMARY is your highest-priority objective — it drives what gets prescribed."
        : null;

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader title="Objectives" subtitle="A race, a meet, a Hyrox, a PR — the targets your training is pointed at.">
        {/* Check-in writes wellness: signed-in athletes only, as on the other console screens. */}
        {token != null && (
          <button onClick={actions.openCheckin} className="rounded-[9px] border border-white/[0.07] bg-white/[0.04] px-[14px] py-[9px] text-[12.5px] font-semibold leading-none text-soft">
            Check in
          </button>
        )}
        <button onClick={actions.openObjectiveCreate} className="rounded-[9px] border border-white/[0.07] bg-white/[0.04] px-[14px] py-[9px] text-[12.5px] font-semibold leading-none text-soft">
          New objective →
        </button>
        <button onClick={actions.openLog} className="rounded-[9px] bg-ac px-[15px] py-[9px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Log workout</button>
      </ScreenHeader>

      {mutateError && (
        <div role="alert" className="rounded-[11px] border border-hot/25 bg-hot/[0.08] px-[14px] py-[11px] text-[12px] font-medium leading-[1.5] text-hot">{mutateError}</div>
      )}

      <ProgramHero objectives={objectives} />

      {active.length > 0 && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <SectionLabel className={LABEL}>Display order</SectionLabel>
            <span className="text-[11px] font-medium leading-none text-dim">
              drag or use ↑ ↓ to reorder · display only — it doesn’t change what drives training
            </span>
          </div>
          {drivingWhy && <p className="m-0 text-[11.5px] font-medium leading-[1.5] text-faint">{drivingWhy}</p>}

          <ol aria-label="Objective display order" className="m-0 flex list-none flex-col gap-[10px] p-0">
            {active.map((o, i) => (
              <li
                key={o.id}
                data-testid="objective-row"
                draggable={!saving && active.length > 1}
                onDragStart={(e: DragEvent<HTMLLIElement>) => {
                  setDragId(o.id);
                  e.dataTransfer.effectAllowed = "move";
                  e.dataTransfer.setData("text/plain", String(o.id));
                }}
                onDragOver={(e: DragEvent<HTMLLIElement>) => {
                  if (dragId == null) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                  if (overId !== o.id) setOverId(o.id);
                }}
                onDragLeave={() => {
                  if (overId === o.id) setOverId(null);
                }}
                onDrop={(e: DragEvent<HTMLLIElement>) => {
                  e.preventDefault();
                  dropOn(o.id);
                }}
                onDragEnd={() => {
                  setDragId(null);
                  setOverId(null);
                }}
                className={cn(dragId === o.id && "opacity-50", overId === o.id && dragId !== o.id && "rounded-[16px] ring-1 ring-ac/60")}
              >
                <ActiveObjectiveCard
                  o={o}
                  rank={i + 1}
                  isFirst={i === 0}
                  isLast={i === active.length - 1}
                  primary={o.id === drivingId}
                  evidence={o.benchmark_code ? evidence.get(o.benchmark_code) : undefined}
                  reorderDisabled={saving || active.length < 2}
                  busy={mutatingId === o.id}
                  onMove={(d) => move(o.id, d)}
                  onAchieve={() => markAchieved(o.id)}
                  onDelete={() => remove(o.id)}
                />
              </li>
            ))}
          </ol>
          <div aria-live="polite" className="sr-only">{announce}</div>
        </div>
      )}

      {closed.length > 0 && (
        <div className="flex flex-col gap-3">
          <SectionLabel className={LABEL}>Closed</SectionLabel>
          <div className="grid grid-cols-1 gap-[10px] lg:grid-cols-2">
            {closed.map((o) => (
              <ClosedObjectiveCard key={o.id} o={o} busy={mutatingId === o.id} onDelete={() => remove(o.id)} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Program hero
// ---------------------------------------------------------------------------

const TIMELINE_LEGEND: LegendItem[] = [
  { label: "completed", color: "color-mix(in srgb, var(--ac) 35%, transparent)" },
  { label: "current block", color: "var(--ac)" },
  { label: "benchmark week", color: "var(--color-cat-5)" },
  { label: "taper · block's final week", color: "var(--color-warn)" },
  { label: "unplanned · not generated yet", color: "color-mix(in srgb, var(--color-faint) 25%, transparent)" },
];

function ProgramHero({ objectives }: { objectives: ObjectiveRead[] }) {
  const { state, actions } = usePerfLab();
  const macrosRes = useAuthedResource<MacrocycleRead[]>(
    (t) => api.listMacrocycles(t),
    [state.macrocyclesRefreshKey],
  );

  return (
    <ResourceState
      resource={macrosRes}
      isEmpty={(rows) => rows.length === 0}
      variant="box"
      className="min-h-[140px]"
      guest={{ body: "Sign in to track a program across your blocks." }}
      loadingContent={{ body: "Loading your program…" }}
      empty={{
        title: "No program yet",
        body: "Anchor a program to an objective to track a real week X of Y across every block.",
        action: { label: "New program →", onClick: actions.openMacrocycleCreate },
      }}
      error={{ title: "Couldn't load your program" }}
      staleLabel="Couldn't refresh your program — showing your last loaded weeks."
    >
      {(macros) => {
        const m = activeMacrocycle(macros);
        return m ? <ProgramCard m={m} anchor={objectives.find((o) => o.id === m.objective_id) ?? null} /> : null;
      }}
    </ResourceState>
  );
}

function ProgramCard({ m, anchor }: { m: MacrocycleRead; anchor: ObjectiveRead | null }) {
  const wp = m.week_progress;
  const tl = programTimeline(m);
  const blocks = m.block_count;
  const daysToGo = anchor?.days_to_go ?? null;

  return (
    <Card
      className="bg-[radial-gradient(120%_140%_at_100%_0%,color-mix(in_srgb,var(--ac)_12%,transparent),transparent_55%)] px-[22px] py-5"
      hover={false}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <SectionLabel className="text-[10px] text-ac">Program · anchored to {m.objective_label}</SectionLabel>
          <div className="mt-[10px] text-[19px] font-bold leading-none tracking-[-0.01em] text-ink">
            Week {wp.current_week}
            {wp.total_weeks != null ? ` of ${wp.total_weeks}` : ""} · {blocks} block{blocks === 1 ? "" : "s"}
          </div>
        </div>
        <div className="flex-none text-right">
          {daysToGo != null && m.target_date ? (
            <>
              <div className="font-mono text-[34px] font-semibold leading-none text-ink">{daysToGo}</div>
              <div className="mt-[5px] font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">
                days to {shortDate(m.target_date)}
              </div>
            </>
          ) : (
            <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">open horizon</div>
          )}
        </div>
      </div>

      {tl.segments.length > 0 ? (
        <div className="relative mt-7">
          <div className="flex gap-1" role="list" aria-label="Program blocks">
            {tl.segments.map((s) => (
              <TimelineBlock key={s.key} s={s} />
            ))}
          </div>
          {tl.nowPct != null && (
            <>
              <div aria-hidden className="absolute -top-[6px] h-[42px] w-[2px] bg-ink" style={{ left: `${tl.nowPct}%` }} />
              <div
                data-testid="now-pin"
                className="absolute -top-5 -translate-x-1/2 whitespace-nowrap rounded-[5px] bg-ink px-[6px] py-[3px] font-mono text-[8.5px] leading-none text-canvas"
                style={{ left: `${tl.nowPct}%` }}
              >
                NOW · wk {wp.current_week}
              </div>
            </>
          )}
        </div>
      ) : (
        <p className="m-0 mt-5 text-[12px] font-medium leading-[1.5] text-mute">
          No blocks under this program yet — generate a block to start the timeline.
        </p>
      )}

      <Legend items={TIMELINE_LEGEND} className="mt-[22px] border-t border-white/[0.07] pt-[14px]" />
    </Card>
  );
}

function TimelineBlock({ s }: { s: TimelineSegment }) {
  const tone =
    s.kind === "unplanned"
      ? "border-dashed border-white/[0.16] bg-transparent text-faint"
      : s.phase === "current"
        ? "border-ac bg-ac text-[#0a0c10]"
        : s.phase === "completed"
          ? "border-white/[0.07] bg-ac/35 text-ink"
          : "border-white/[0.07] bg-white/[0.04] text-mute";
  const pct = (week: number) => `${((week - 1) / s.weeks) * 100}%`;
  return (
    <div
      role="listitem"
      data-testid="timeline-segment"
      data-kind={s.kind}
      data-phase={s.phase ?? "unplanned"}
      data-weeks={s.weeks}
      className="min-w-0"
      style={{ flex: `${s.weeks} ${s.weeks} 0%` }}
      aria-label={`${s.label}: ${s.meta}`}
    >
      <div className={cn("relative flex h-[30px] items-center overflow-hidden rounded-[8px] border px-[10px]", tone)}>
        {s.taperWeek != null && (
          <span
            aria-hidden
            data-testid="taper-week"
            className="absolute inset-y-0 right-0 border-l border-warn/40 bg-warn/[0.18]"
            style={{ left: pct(s.taperWeek) }}
          />
        )}
        {s.benchmarkWeeks.map((w) => (
          <span
            key={w}
            aria-hidden
            data-testid="benchmark-week"
            className="absolute bottom-[3px] h-[3px] rounded-full bg-cat-5"
            style={{ left: pct(w), width: `${100 / s.weeks}%` }}
          />
        ))}
        <span className="relative truncate text-[11px] font-semibold leading-none">{s.label}</span>
      </div>
      <div className="mt-[7px] truncate font-mono text-[9.5px] leading-none text-dim">{s.meta}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Objective cards
// ---------------------------------------------------------------------------

function EvidenceChip({ status }: { status: EvidenceStatus }) {
  const tone =
    status === "established"
      ? "border-good/30 bg-good/10 text-good"
      : status === "provisional"
        ? "border-warn/30 bg-warn/10 text-warn"
        : "border-white/[0.07] bg-white/[0.04] text-faint";
  return (
    <span
      data-testid="evidence-chip"
      className={cn("rounded-[5px] border px-[6px] py-[3px] font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.08em]", tone)}
    >
      {status ?? "not measured"}
    </span>
  );
}

function progressLabel(o: ObjectiveRead): string {
  const { current, target, pct } = o.progress;
  if (current != null && target != null) return `${current} / ${target}`;
  return pct != null ? `${Math.round(pct)}%` : "";
}

function ActiveObjectiveCard({
  o,
  rank,
  isFirst,
  isLast,
  primary,
  evidence,
  reorderDisabled,
  busy,
  onMove,
  onAchieve,
  onDelete,
}: {
  o: ObjectiveRead;
  rank: number;
  isFirst: boolean;
  isLast: boolean;
  primary: boolean;
  /** undefined: no linked benchmark, or it isn't in the assessment surface. */
  evidence: EvidenceStatus | undefined;
  reorderDisabled: boolean;
  busy: boolean;
  onMove: (delta: -1 | 1) => void;
  onAchieve: () => void;
  onDelete: () => void;
}) {
  const hasTarget = o.target_value != null;
  const pct = o.progress.pct;
  const moveBtn =
    "grid h-[20px] w-[22px] place-items-center rounded-[6px] border border-white/[0.07] bg-white/[0.04] text-[10px] leading-none text-mute hover:text-ink disabled:opacity-30";

  return (
    <div
      data-card="1"
      data-primary={primary ? "1" : "0"}
      className={cn(
        "flex items-stretch gap-4 rounded-[16px] border px-[18px] py-4",
        primary ? "border-ac/35 bg-ac/[0.04]" : "border-white/[0.07] bg-tile",
      )}
    >
      <div className="flex flex-none flex-col items-center justify-center gap-[6px]">
        <span className={cn("font-mono text-[15px] font-semibold leading-none", primary ? "text-ac" : "text-faint")} aria-label={`Display position ${rank}`}>
          {rank}
        </span>
        <span aria-hidden className="cursor-grab text-[13px] leading-none text-dim">⋮⋮</span>
        <div className="flex flex-col gap-[3px]">
          <button type="button" className={moveBtn} aria-label={`Move ${o.label} up`} disabled={reorderDisabled || isFirst} onClick={() => onMove(-1)}>
            ↑
          </button>
          <button type="button" className={moveBtn} aria-label={`Move ${o.label} down`} disabled={reorderDisabled || isLast} onClick={() => onMove(1)}>
            ↓
          </button>
        </div>
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[16px] font-bold leading-none text-ink">{o.label}</span>
          <Pill>{domainLabel(o.domain)}</Pill>
          {primary && (
            <span
              data-testid="primary-badge"
              title="Drives what gets prescribed"
              className="rounded-[7px] bg-ac px-[7px] py-[5px] font-mono text-[10px] font-bold leading-none tracking-[0.08em] text-[#0a0c10] shadow-[0_6px_18px_-8px_var(--ac)]"
            >
              PRIMARY
            </span>
          )}
        </div>

        <div className="mt-[9px] flex flex-wrap items-center gap-[14px] text-[11.5px] font-medium leading-none text-faint">
          <span>
            By <span className="text-soft">{o.target_date ? longDate(o.target_date) : "open"}</span>
          </span>
          {hasTarget && (
            <span>
              Target <span className="text-soft">{o.target_value}{o.target_unit ? ` ${o.target_unit}` : ""}</span>
            </span>
          )}
          <span>
            Priority <span className="text-soft">{o.priority}</span>
          </span>
          {o.benchmark_code && (
            <span className="flex items-center gap-[6px]">
              Linked benchmark
              {evidence !== undefined && <EvidenceChip status={evidence} />}
            </span>
          )}
        </div>

        {pct != null && (
          <div className="mt-3 flex items-center gap-3">
            <div className="flex-1">
              <Track pct={Math.max(0, Math.min(100, pct))} />
            </div>
            <span className="flex-none font-mono text-[11px] font-semibold leading-none text-soft">{progressLabel(o)}</span>
          </div>
        )}

        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={onAchieve}
            disabled={busy}
            className="rounded-[8px] border border-good/30 bg-good/[0.1] px-[10px] py-[7px] text-[11.5px] font-semibold leading-none text-good disabled:opacity-50"
          >
            Mark achieved
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={busy}
            className="rounded-[8px] border border-white/[0.07] bg-white/[0.04] px-[10px] py-[7px] text-[11.5px] font-semibold leading-none text-mute disabled:opacity-50"
          >
            Delete
          </button>
        </div>
      </div>

      <div className="flex-none text-right">
        <div className="font-mono text-[26px] font-semibold leading-none text-ink">{o.days_to_go ?? "—"}</div>
        <div className="mt-[6px] font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">days to go</div>
      </div>
    </div>
  );
}

function ClosedObjectiveCard({ o, busy, onDelete }: { o: ObjectiveRead; busy: boolean; onDelete: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[16px] border border-white/[0.07] bg-tile px-[18px] py-[14px]">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-[14px] font-bold leading-none text-soft">{o.label}</span>
          <Pill>{domainLabel(o.domain)}</Pill>
        </div>
        <div className={cn("mt-2 text-[11px] font-medium leading-none", o.status === "achieved" ? "text-good" : "text-faint")}>
          {statusLabel(o.status)}
        </div>
      </div>
      <button
        type="button"
        onClick={onDelete}
        disabled={busy}
        className="flex-none rounded-[8px] border border-white/[0.07] bg-white/[0.04] px-[10px] py-[7px] text-[11.5px] font-semibold leading-none text-mute disabled:opacity-50"
      >
        Delete
      </button>
    </div>
  );
}

function TargetIcon() {
  return (
    <div className="grid h-[60px] w-[60px] place-items-center rounded-[16px] border border-ac/25 bg-ac/[0.1]">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="1.6">
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="5" />
        <circle cx="12" cy="12" r="1.2" fill="var(--ac)" stroke="none" />
      </svg>
    </div>
  );
}
