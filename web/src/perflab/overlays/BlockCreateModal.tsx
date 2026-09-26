// src/perflab/overlays/BlockCreateModal.tsx
//
// Block-creation overlay (POST /v1/planning/blocks). This is the fix for the
// Planning dead-end: a fresh signed-in athlete has no block and no way to make
// one, so the screen quietly fell back to a hard-coded prototype week. This
// modal exposes the fields the backend needs to generate a real weekly
// template, including the Phase 3a per-block session preferences (target
// session length + accessory emphasis/focus).
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/auth/useAuth";
import { createPlanningBlock, previewPlanningBlock } from "@/api/perfLabClient";
import type { ApiError, BlockGoal, WeeklyTemplateSlot } from "@/types";
import { usePerfLab } from "../store";
import { CloseBtn } from "./LogWorkoutModal";

import {
  BLOCK_GOALS,
  buildBlockCreateRequest,
  EMPHASIS,
  FOCUS_TAGS,
  GOAL_DOMAIN,
  initialForm,
  INTENSITIES,
  RUNNING_FOCUS,
  SECONDARY_STYLES,
  type BlockForm,
} from "./blockCreateBody";

const DAY_LABELS: Record<number, string> = {
  1: "Monday",
  2: "Tuesday",
  3: "Wednesday",
  4: "Thursday",
  5: "Friday",
  6: "Saturday",
  7: "Sunday",
};


const inputCls = "mt-2 w-full rounded-[11px] border border-white/10 bg-panel px-[13px] py-[11px] text-[14px] text-ink";
const segCls = (active: boolean) =>
  cn(
    "flex-1 cursor-pointer rounded-[10px] border p-[11px] text-center text-[13px] font-semibold leading-none",
    active ? "border-ac/40 bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute",
  );
const chipCls = (active: boolean) =>
  cn(
    "cursor-pointer rounded-[9px] border px-[13px] py-[9px] text-[12px] font-semibold leading-none",
    active ? "border-ac/[0.45] bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute",
  );

export function BlockCreateModal() {
  const { state, actions } = usePerfLab();
  const auth = useAuth();
  const profileGoal = auth.profile?.primary_goal ?? null;
  const [form, setForm] = useState<BlockForm>(() => initialForm(profileGoal));
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  // The week the SERVER would generate. Fetched rather than computed here: a style that ends
  // up with zero sessions is a fact the athlete should see before creating the block, and a
  // second largest-remainder implementation in TypeScript would eventually disagree.
  const [preview, setPreview] = useState<WeeklyTemplateSlot[] | null>(null);
  const [previewError, setPreviewError] = useState(false);

  const open = state.blockCreateOpen;
  const token = auth.token;
  const previewKey = open
    ? JSON.stringify({ g: form.goal, f: form.runningFocus, s: form.secondary, n: form.sessionsPerWeek })
    : null;

  // The profile can arrive after first render. Preselect from it only while the form is
  // untouched, so it never overrides a choice the athlete made.
  useEffect(() => {
    if (!open) return;
    setForm((f) =>
      JSON.stringify(f) === JSON.stringify(initialForm()) ? initialForm(profileGoal) : f,
    );
  }, [open, profileGoal]);

  useEffect(() => {
    if (!open || !token || previewKey === null) return;
    let cancelled = false;
    setPreviewError(false);
    previewPlanningBlock(buildBlockCreateRequest(form), token)
      .then((slots) => {
        if (!cancelled) setPreview(slots);
      })
      .catch(() => {
        if (!cancelled) {
          setPreview(null);
          setPreviewError(true);
        }
      });
    return () => {
      cancelled = true;
    };
    // `previewKey` names every input the generated week depends on; `form` is read inside.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, token, previewKey]);

  if (!state.blockCreateOpen) return null;

  const set = <K extends keyof BlockForm>(key: K, value: BlockForm[K]) => setForm((f) => ({ ...f, [key]: value }));
  const toggleSecondary = (domain: string) =>
    setForm((f) => ({
      ...f,
      secondary: f.secondary.includes(domain)
        ? f.secondary.filter((d) => d !== domain)
        : [...f.secondary, domain],
    }));
  const toggleFocus = (tag: string) =>
    setForm((f) => ({ ...f, focus: f.focus.includes(tag) ? f.focus.filter((t) => t !== tag) : [...f.focus, tag] }));

  // Submit → POST /v1/planning/blocks (auth required); on success, bump the
  // refresh key so PlanningScreen's useAuthedResource re-fetches the week.
  async function save() {
    if (!auth.token) {
      actions.closeBlockCreate();
      actions.openAuth();
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const req = buildBlockCreateRequest(form);
      await createPlanningBlock(req, auth.token);
      // Focus the week that contains the new block's start_date (not necessarily
      // the current wall-clock week) so its first sessions are what Planning shows.
      actions.focusPlanningWeek(req.start_date);
      actions.closeBlockCreate();
      setForm(initialForm(profileGoal));
    } catch (e) {
      setSaveError(
        (e as ApiError)?.message ??
          "Couldn't create the block — check you're signed in and the backend is reachable.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[64] flex items-center justify-center p-8 backdrop-blur-[4px]" style={{ background: "rgba(4,5,8,.7)" }}>
      <div className="max-h-[92vh] w-[640px] max-w-full overflow-auto rounded-[18px] border border-white/[0.09] bg-surface shadow-[0_50px_110px_-30px_rgba(0,0,0,.75)]">
        <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-5">
          <div className="flex items-center gap-[10px]">
            <h2 className="m-0 text-[18px] font-bold leading-none tracking-[-0.01em] text-ink">Create a training block</h2>
            <span className="rounded-[7px] border border-mint/25 bg-mint/[0.12] px-2 py-[5px] font-mono text-[10px] font-semibold leading-none tracking-[0.1em] text-[#9ad6c8]">planning/blocks</span>
          </div>
          <CloseBtn onClick={actions.closeBlockCreate} />
        </div>

        <div className="flex flex-col gap-[18px] px-6 py-[22px]">
          <label className="block">
            <span className="text-[12px] font-medium leading-none text-mute">Goal</span>
            <select value={form.goal} onChange={(e) => set("goal", e.target.value as BlockGoal)} className={inputCls} style={{ colorScheme: "dark" }}>
              {BLOCK_GOALS.map((g) => (
                <option key={g.value} value={g.value}>{g.label}</option>
              ))}
            </select>
          </label>

          {form.goal === "Running" && (
            <div>
              <span className="text-[12px] font-medium leading-none text-mute">Running focus</span>
              <div className="mt-2 flex gap-2">
                {RUNNING_FOCUS.map((o) => (
                  <div key={o.value} onClick={() => set("runningFocus", o.value)} className={segCls(form.runningFocus === o.value)}>{o.label}</div>
                ))}
              </div>
              <p className="mt-2 text-[11px] font-medium leading-[1.45] text-faint">
                {RUNNING_FOCUS.find((o) => o.value === form.runningFocus)?.help}
              </p>
            </div>
          )}

          <div>
            <span className="text-[12px] font-medium leading-none text-mute">Also train (optional)</span>
            <p className="mb-1 mt-[6px] text-[11px] font-medium leading-[1.45] text-faint">
              Secondary styles share the week with your main style. Shares are SESSIONS, not time —
              with few sessions a week a style can end up with none, and the preview shows that.
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {SECONDARY_STYLES.filter((o) => o.domain !== GOAL_DOMAIN[form.goal]).map((o) => (
                <div
                  key={o.domain}
                  onClick={() => toggleSecondary(o.domain)}
                  className={chipCls(form.secondary.includes(o.domain))}
                >
                  {o.label}
                </div>
              ))}
            </div>
          </div>

          <div>
            <span className="text-[12px] font-medium leading-none text-mute">This week</span>
            <div className="mt-2 rounded-[11px] border border-white/[0.08] bg-white/[0.02] px-[13px] py-[11px]">
              {previewError ? (
                <span className="text-[11.5px] font-medium leading-[1.45] text-dim">
                  Couldn’t load the preview — the block will still be generated from these settings.
                </span>
              ) : preview === null ? (
                <span className="text-[11.5px] font-medium leading-[1.45] text-dim">Loading…</span>
              ) : (
                <>
                  <div className="flex flex-col gap-[6px]">
                    {preview.map((slot, i) => (
                      <div key={`${slot.day_of_week}-${i}`} className="flex items-center justify-between">
                        <span className="text-[12px] font-medium leading-none text-soft">
                          {DAY_LABELS[slot.day_of_week] ?? `Day ${slot.day_of_week}`}
                        </span>
                        <span className="font-mono text-[11px] leading-none text-mute">{slot.category}</span>
                      </div>
                    ))}
                  </div>
                  {(() => {
                    const planned = new Set(preview.map((s) => s.domain).filter(Boolean));
                    const missed = form.secondary.filter((d) => !planned.has(d));
                    if (missed.length === 0) return null;
                    const labels = missed
                      .map((d) => SECONDARY_STYLES.find((o) => o.domain === d)?.label ?? d)
                      .join(", ");
                    return (
                      <p className="mt-[10px] text-[11px] font-medium leading-[1.45] text-hot">
                        No sessions for {labels} at {form.sessionsPerWeek} per week — add sessions or
                        drop a style.
                      </p>
                    );
                  })()}
                </>
              )}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-[14px]">
            <label className="block">
              <span className="text-[12px] font-medium leading-none text-mute">Start date</span>
              <input type="date" value={form.startDate} onChange={(e) => set("startDate", e.target.value)} className={inputCls} style={{ colorScheme: "dark" }} />
            </label>
            <label className="block">
              <span className="text-[12px] font-medium leading-none text-mute">Duration (weeks)</span>
              <input type="number" min={1} max={24} value={form.durationWeeks} onChange={(e) => set("durationWeeks", e.target.value)} className={inputCls} />
            </label>
            <label className="block">
              <span className="text-[12px] font-medium leading-none text-mute">Sessions / week</span>
              <input type="number" min={1} max={7} value={form.sessionsPerWeek} onChange={(e) => set("sessionsPerWeek", e.target.value)} className={inputCls} />
            </label>
            <label className="block">
              <span className="text-[12px] font-medium leading-none text-mute">Target session length (min)</span>
              <input type="number" min={20} max={180} placeholder="Optional" value={form.targetMinutes} onChange={(e) => set("targetMinutes", e.target.value)} className={inputCls} />
            </label>
          </div>

          <div>
            <span className="text-[12px] font-medium leading-none text-mute">Workload</span>
            <div className="mt-2 flex gap-2">
              {INTENSITIES.map((o) => (
                <div key={o.value} onClick={() => set("intensity", o.value)} className={segCls(form.intensity === o.value)}>{o.label}</div>
              ))}
            </div>
            <p className="mt-2 text-[11px] font-medium leading-[1.45] text-faint">
              {INTENSITIES.find((o) => o.value === form.intensity)?.help}
            </p>
            <p className="mt-1 text-[11px] font-medium leading-[1.45] text-dim">
              Currently adjusts strength-type sessions only.
            </p>
          </div>

          <div>
            <span className="text-[12px] font-medium leading-none text-mute">Accessory emphasis</span>
            <div className="mt-2 flex gap-2">
              {EMPHASIS.map((o) => (
                <div key={o.value} onClick={() => set("emphasis", o.value)} className={segCls(form.emphasis === o.value)}>{o.label}</div>
              ))}
            </div>
          </div>

          <div>
            <span className="text-[12px] font-medium leading-none text-mute">Accessory focus</span>
            <div className="mt-2 flex flex-wrap gap-2">
              {FOCUS_TAGS.map((t) => (
                <div key={t.value} onClick={() => toggleFocus(t.value)} className={chipCls(form.focus.includes(t.value))}>{t.label}</div>
              ))}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between gap-[9px] border-t border-white/[0.06] px-6 py-4">
          <span className={cn("max-w-[330px] text-[11px] font-medium leading-[1.4]", saveError ? "text-hot" : "text-dim")}>
            {saveError ?? "Generates a weekly template and this week's sessions from your goal and cadence."}
          </span>
          <div className="flex flex-none gap-[9px]">
            <button onClick={actions.closeBlockCreate} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft">Cancel</button>
            <button onClick={save} disabled={saving} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-[18px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60">
              {saving ? "Creating…" : auth.token ? "Create block →" : "Sign in to create →"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
