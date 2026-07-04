// src/perflab/screens/ObjectivesScreen.tsx
//
// Objectives (P4a/P4b): a multi-domain goal — a race, a strength meet, a
// Hyrox, a benchmark PR — replaces the running-only, frontend-only "Goal
// Race" screen and its hard-coded Valencia Marathon mock. Backed by
// GET/POST /v1/objectives and PATCH/DELETE /v1/objectives/{id}.
import { useState } from "react";
import * as api from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import type { ApiError, ObjectiveRead } from "@/types";
import { usePerfLab } from "../store";
import { useAuthedResource } from "../useAuthedResource";
import { sortObjectives } from "../objectives";
import { Card, Pill, ScreenHeader, Track } from "../ui";

const DOMAIN_LABELS: Record<string, string> = {
  general: "General",
  strength: "Strength",
  powerlifting: "Powerlifting",
  running: "Running",
  hyrox: "Hyrox",
};

function domainLabel(domain: string | null): string {
  if (!domain) return "General";
  return DOMAIN_LABELS[domain.toLowerCase()] ?? domain;
}

function statusLabel(status: ObjectiveRead["status"]): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function statusColor(status: ObjectiveRead["status"]): string {
  if (status === "achieved") return "text-good";
  if (status === "abandoned") return "text-faint";
  return "text-ac";
}

export function ObjectivesScreen() {
  const { state, actions } = usePerfLab();
  const auth = useAuth();
  const [mutatingId, setMutatingId] = useState<number | null>(null);
  const [mutateError, setMutateError] = useState<string | null>(null);

  const { data: objectives, loading, error } = useAuthedResource<ObjectiveRead[]>(
    (t) => api.listObjectives(t),
    [state.objectivesRefreshKey],
  );

  async function markAchieved(id: number) {
    if (!auth.token) return;
    setMutatingId(id);
    setMutateError(null);
    try {
      await api.updateObjective(id, { status: "achieved" }, auth.token);
      actions.refreshObjectives();
    } catch (e) {
      setMutateError((e as ApiError)?.message ?? "Couldn't update that objective.");
    } finally {
      setMutatingId(null);
    }
  }

  async function remove(id: number) {
    if (!auth.token) return;
    setMutatingId(id);
    setMutateError(null);
    try {
      await api.deleteObjective(id, auth.token);
      actions.refreshObjectives();
    } catch (e) {
      setMutateError((e as ApiError)?.message ?? "Couldn't delete that objective.");
    } finally {
      setMutatingId(null);
    }
  }

  // Signed-out: gate rather than show empty/mock content — objectives have no
  // frontend-only fallback anymore.
  if (!auth.token) {
    return (
      <Notice
        title="Sign in to set your objectives"
        body="Objectives — a race, a meet, a Hyrox, a benchmark PR — live on your account so your plan can point at them."
        action={{ label: "Sign in →", onClick: actions.openAuth }}
      />
    );
  }

  if (error) {
    return <Notice title="Couldn't load your objectives" body={error} action={{ label: "Retry", onClick: actions.refreshObjectives }} />;
  }

  // `useAuthedResource` first-renders with loading:false before its effect runs,
  // so treat "not yet resolved" (null data, no error) as loading too — otherwise
  // the empty-state CTA flashes for one frame (mirrors PlanningScreen).
  if (loading || objectives === null) {
    return <Notice title="Loading your objectives…" body="Fetching what your plan is pointed at." />;
  }

  if (objectives.length === 0) {
    return (
      <Notice
        title="Set your first objective"
        body="A race, a meet, a Hyrox, a lift PR — give your plan something to point at, benchmark-linked or free-text."
        action={{ label: "New objective →", onClick: actions.openObjectiveCreate, primary: true }}
      />
    );
  }

  const sorted = sortObjectives(objectives);

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader title="Objectives" subtitle="A race, a meet, a Hyrox, a PR — everything your plan is pointed at.">
        <button onClick={actions.openObjectiveCreate} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">New objective →</button>
      </ScreenHeader>

      {mutateError && (
        <div className="rounded-[11px] border border-hot/25 bg-hot/[0.08] px-[14px] py-[11px] text-[12px] font-medium leading-[1.5] text-hot">{mutateError}</div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {sorted.map((o) => (
          <ObjectiveCard key={o.id} o={o} busy={mutatingId === o.id} onAchieve={() => markAchieved(o.id)} onDelete={() => remove(o.id)} />
        ))}
      </div>
    </section>
  );
}

function ObjectiveCard({
  o,
  busy,
  onAchieve,
  onDelete,
}: {
  o: ObjectiveRead;
  busy: boolean;
  onAchieve: () => void;
  onDelete: () => void;
}) {
  const hasTarget = o.target_value != null;
  const hasProgress = o.progress.pct != null;

  return (
    <Card className="flex flex-col gap-[14px] p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-[8px]">
            <span className="text-[16px] font-bold leading-none text-ink">{o.label}</span>
            <Pill>{domainLabel(o.domain)}</Pill>
          </div>
          <div className="mt-2 flex items-center gap-[10px] text-[11px] font-medium leading-none text-faint">
            <span>Priority {o.priority}</span>
            <span className="text-[#3a4049]">·</span>
            <span className={statusColor(o.status)}>{statusLabel(o.status)}</span>
          </div>
        </div>
        {o.days_to_go != null && (
          <div className="flex-none text-right">
            <div className="font-mono text-[26px] font-semibold leading-none text-ink">{o.days_to_go}</div>
            <div className="mt-1 font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">days to go</div>
          </div>
        )}
      </div>

      {(hasTarget || o.target_date) && (
        <div className="flex flex-wrap items-center gap-[18px] border-t border-white/[0.05] pt-[12px] text-[12.5px] font-medium leading-none text-mute">
          {hasTarget && (
            <span>
              Target <span className="text-soft">{o.target_value}{o.target_unit ? ` ${o.target_unit}` : ""}</span>
            </span>
          )}
          {o.target_date && (
            <span>
              By <span className="text-soft">{o.target_date}</span>
            </span>
          )}
        </div>
      )}

      {hasProgress && (
        <div>
          <div className="mb-[6px] flex items-center justify-between text-[11px] font-medium leading-none text-faint">
            <span>Progress{o.progress.direction ? ` · ${o.progress.direction}` : ""}</span>
            <span className="text-soft">{o.progress.current ?? "—"} / {o.progress.target ?? "—"}</span>
          </div>
          <Track pct={Math.max(0, Math.min(100, o.progress.pct as number))} />
        </div>
      )}

      <div className="mt-auto flex gap-[9px] pt-1">
        {o.status === "active" && (
          <button
            onClick={onAchieve}
            disabled={busy}
            className="rounded-[9px] border border-good/30 bg-good/[0.1] px-[13px] py-[9px] text-[12px] font-semibold leading-none text-good disabled:opacity-50"
          >
            Mark achieved
          </button>
        )}
        <button
          onClick={onDelete}
          disabled={busy}
          className="rounded-[9px] border border-white/10 bg-white/[0.03] px-[13px] py-[9px] text-[12px] font-semibold leading-none text-mute disabled:opacity-50"
        >
          Delete
        </button>
      </div>
    </Card>
  );
}

// Shared loading / error / gate / empty notice — kept visually distinct so a
// fetch that's still in-flight or that errored is never mistaken for "no
// objectives yet" (mirrors PlanningScreen's PlanningNotice).
function Notice({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: { label: string; onClick: () => void; primary?: boolean };
}) {
  return (
    <section className="flex min-h-[70vh] items-center justify-center px-[30px] pb-9 pt-[26px]">
      <Card className="flex max-w-[520px] flex-col items-center gap-4 p-[44px] text-center">
        <div className="grid h-[60px] w-[60px] place-items-center rounded-[16px] border border-ac/25 bg-ac/[0.1]">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="1.6">
            <circle cx="12" cy="12" r="9" />
            <circle cx="12" cy="12" r="5" />
            <circle cx="12" cy="12" r="1.2" fill="var(--ac)" stroke="none" />
          </svg>
        </div>
        <div className="text-[22px] font-bold leading-[1.2] text-ink">{title}</div>
        <div className="max-w-[380px] text-[13.5px] font-medium leading-[1.6] text-[#7c818c]">{body}</div>
        {action && (
          <button
            onClick={action.onClick}
            className={
              action.primary
                ? "mt-[6px] rounded-[10px] bg-gradient-to-r from-ac to-[#a7e36e] px-5 py-3 text-[13px] font-semibold leading-none text-[#0a0c10]"
                : "mt-[6px] rounded-[10px] border border-white/10 bg-white/[0.04] px-5 py-3 text-[13px] font-semibold leading-none text-soft"
            }
          >
            {action.label}
          </button>
        )}
      </Card>
    </section>
  );
}
