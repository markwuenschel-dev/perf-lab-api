// src/perflab/overlays/BlockCreateModal.tsx
//
// Block-creation overlay (POST /v1/planning/blocks). This is the fix for the
// Planning dead-end: a fresh signed-in athlete has no block and no way to make
// one, so the screen quietly fell back to a hard-coded prototype week. This
// modal exposes the fields the backend needs to generate a real weekly
// template, including the Phase 3a per-block session preferences (target
// session length + accessory emphasis/focus).
import { useState } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/auth/useAuth";
import { createPlanningBlock } from "@/api/perfLabClient";
import type { ApiError, BlockCreateRequest, BlockGoal } from "@/types";
import { usePerfLab } from "../store";
import { CloseBtn } from "./LogWorkoutModal";

// BlockGoal is a smaller, block-scoped enum — NOT the 14-value athlete
// TRAINING_GOALS in store.tsx. Kept in sync with the `BlockGoal` schema
// (types.gen.ts); if the backend adds a value, add it here too.
const BLOCK_GOALS: { value: BlockGoal; label: string }[] = [
  { value: "General", label: "General" },
  { value: "Strength", label: "Strength" },
  { value: "Hypertrophy", label: "Hypertrophy" },
  { value: "Power", label: "Power" },
  { value: "Hyrox", label: "Hyrox" },
  { value: "CrossFit", label: "CrossFit" },
  { value: "Running", label: "Running" },
  { value: "Calisthenics", label: "Calisthenics" },
  { value: "Recomp", label: "Recomp" },
];

type Emphasis = "minimal" | "balanced" | "high";
const EMPHASIS: { value: Emphasis; label: string }[] = [
  { value: "minimal", label: "Minimal" },
  { value: "balanced", label: "Balanced" },
  { value: "high", label: "High" },
];

// Accessory focus tags the backend understands (`_ACCESSORY_BY_TAG`).
const FOCUS_TAGS: { value: string; label: string }[] = [
  { value: "posterior_chain", label: "Posterior chain" },
  { value: "push", label: "Push" },
  { value: "pull", label: "Pull" },
  { value: "core", label: "Core" },
  { value: "single_leg", label: "Single-leg" },
];

const todayIso = (): string => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

const clamp = (n: number, min: number, max: number): number => (Number.isNaN(n) ? min : Math.min(max, Math.max(min, n)));

interface BlockForm {
  goal: BlockGoal;
  startDate: string;
  durationWeeks: number;
  sessionsPerWeek: number;
  targetMinutes: string; // raw input text; "" → omit/null
  emphasis: Emphasis;
  focus: string[];
}

function initialForm(): BlockForm {
  return {
    goal: "General",
    startDate: todayIso(),
    durationWeeks: 8,
    sessionsPerWeek: 3,
    targetMinutes: "",
    emphasis: "balanced",
    focus: [],
  };
}

/** Build the backend BlockCreateRequest from the form. Leaves weekly_template
 *  empty and modality_mix empty — the backend derives the template from goal
 *  + modality_mix (defaulted server-side when empty). */
function buildBlockCreateRequest(f: BlockForm): BlockCreateRequest {
  const trimmed = f.targetMinutes.trim();
  const minutes = trimmed === "" ? null : clamp(Number(trimmed), 20, 180);
  return {
    goal: f.goal,
    start_date: f.startDate,
    duration_weeks: f.durationWeeks,
    sessions_per_week: f.sessionsPerWeek,
    weekly_template: [],
    modality_mix: {},
    target_session_minutes: minutes,
    accessory_emphasis: f.emphasis,
    accessory_focus: f.focus.length > 0 ? f.focus : null,
    deload_every_n_weeks: 4,
    deload_volume_factor: 0.6,
    benchmark_every_n_weeks: 4,
  } satisfies BlockCreateRequest;
}

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
  const [form, setForm] = useState<BlockForm>(initialForm);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  if (!state.blockCreateOpen) return null;

  const set = <K extends keyof BlockForm>(key: K, value: BlockForm[K]) => setForm((f) => ({ ...f, [key]: value }));
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
      await createPlanningBlock(buildBlockCreateRequest(form), auth.token);
      actions.bumpPlanningRefresh();
      actions.closeBlockCreate();
      setForm(initialForm());
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
    <div className="fixed inset-0 z-[63] flex items-center justify-center p-8 backdrop-blur-[4px]" style={{ background: "rgba(4,5,8,.7)" }}>
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

          <div className="grid grid-cols-2 gap-[14px]">
            <label className="block">
              <span className="text-[12px] font-medium leading-none text-mute">Start date</span>
              <input type="date" value={form.startDate} onChange={(e) => set("startDate", e.target.value)} className={inputCls} style={{ colorScheme: "dark" }} />
            </label>
            <label className="block">
              <span className="text-[12px] font-medium leading-none text-mute">Duration (weeks)</span>
              <input type="number" min={1} max={24} value={form.durationWeeks} onChange={(e) => set("durationWeeks", clamp(+e.target.value, 1, 24))} className={inputCls} />
            </label>
            <label className="block">
              <span className="text-[12px] font-medium leading-none text-mute">Sessions / week</span>
              <input type="number" min={1} max={7} value={form.sessionsPerWeek} onChange={(e) => set("sessionsPerWeek", clamp(+e.target.value, 1, 7))} className={inputCls} />
            </label>
            <label className="block">
              <span className="text-[12px] font-medium leading-none text-mute">Target session length (min)</span>
              <input type="number" min={20} max={180} placeholder="Optional" value={form.targetMinutes} onChange={(e) => set("targetMinutes", e.target.value)} className={inputCls} />
            </label>
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
