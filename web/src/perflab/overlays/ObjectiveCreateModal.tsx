// src/perflab/overlays/ObjectiveCreateModal.tsx
//
// New-objective overlay (POST /v1/objectives). An objective can be
// benchmark-linked (progress computes automatically from `benchmark_code`) or
// free-text (countdown-only via `days_to_go`) — a race, a strength meet, a
// Hyrox, or a lift PR are all the same shape.
import { useState } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/auth/useAuth";
import { createObjective } from "@/api/perfLabClient";
import type { ApiError, ObjectiveCreate } from "@/types";
import { usePerfLab } from "../store";
import { DOMAIN_OPTIONS } from "../domains";
import { CloseBtn } from "./LogWorkoutModal";

// Canonical domains only (+ an explicit "no domain" → free-text objective). Sourcing
// from the shared list avoids the old `hyrox`/custom values the backend rejects with 400.
const DOMAINS: { value: string; label: string }[] = [
  { value: "none", label: "No specific domain" },
  ...DOMAIN_OPTIONS,
];

const clamp = (n: number, min: number, max: number): number => (Number.isNaN(n) ? min : Math.min(max, Math.max(min, n)));

interface ObjectiveForm {
  label: string;
  domain: string;
  // Numeric/date fields are held as raw input text so they tolerate a
  // transient blank while retyping; parsed at submit time.
  targetValue: string;
  targetUnit: string;
  targetDate: string;
  priority: string;
  benchmarkCode: string;
}

function initialForm(): ObjectiveForm {
  return {
    label: "",
    domain: "general",
    targetValue: "",
    targetUnit: "",
    targetDate: "",
    priority: "3",
    benchmarkCode: "",
  };
}

/** Build the backend ObjectiveCreate from the form. Unset optionals go as
 *  `null`, never `0`/`""`. */
function buildObjectiveCreateRequest(f: ObjectiveForm): ObjectiveCreate {
  const domain = f.domain === "none" ? null : f.domain;
  const targetValue = f.targetValue.trim();
  const targetUnit = f.targetUnit.trim();
  const benchmarkCode = f.benchmarkCode.trim();
  return {
    label: f.label.trim(),
    domain,
    target_value: targetValue === "" ? null : Number(targetValue),
    target_unit: targetUnit === "" ? null : targetUnit,
    target_date: f.targetDate === "" ? null : f.targetDate,
    priority: clamp(Number(f.priority.trim() || "3"), 1, 5),
    benchmark_code: benchmarkCode === "" ? null : benchmarkCode,
  } satisfies ObjectiveCreate;
}

const inputCls = "mt-2 w-full rounded-[11px] border border-white/10 bg-panel px-[13px] py-[11px] text-[14px] text-ink";

export function ObjectiveCreateModal() {
  const { state, actions } = usePerfLab();
  const auth = useAuth();
  const [form, setForm] = useState<ObjectiveForm>(initialForm);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  if (!state.objectiveCreateOpen) return null;

  const set = <K extends keyof ObjectiveForm>(key: K, value: ObjectiveForm[K]) => setForm((f) => ({ ...f, [key]: value }));
  const labelValid = form.label.trim().length > 0;

  // Submit → POST /v1/objectives (auth required); on success, bump the
  // refresh key so ObjectivesScreen's useAuthedResource re-fetches.
  async function save() {
    if (!auth.token) {
      actions.closeObjectiveCreate();
      actions.openAuth();
      return;
    }
    if (!labelValid) {
      setSaveError("Give the objective a label.");
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const req = buildObjectiveCreateRequest(form);
      await createObjective(req, auth.token);
      actions.refreshObjectives();
      actions.closeObjectiveCreate();
      setForm(initialForm());
    } catch (e) {
      setSaveError(
        (e as ApiError)?.message ??
          "Couldn't create the objective — check you're signed in and the backend is reachable.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[64] flex items-center justify-center p-8 backdrop-blur-[4px]" style={{ background: "rgba(4,5,8,.7)" }}>
      <div className="max-h-[92vh] w-[560px] max-w-full overflow-auto rounded-[18px] border border-white/[0.09] bg-surface shadow-[0_50px_110px_-30px_rgba(0,0,0,.75)]">
        <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-5">
          <div className="flex items-center gap-[10px]">
            <h2 className="m-0 text-[18px] font-bold leading-none tracking-[-0.01em] text-ink">New objective</h2>
            <span className="rounded-[7px] border border-mint/25 bg-mint/[0.12] px-2 py-[5px] font-mono text-[10px] font-semibold leading-none tracking-[0.1em] text-[#9ad6c8]">objectives</span>
          </div>
          <CloseBtn onClick={actions.closeObjectiveCreate} />
        </div>

        <div className="flex flex-col gap-[18px] px-6 py-[22px]">
          <label className="block">
            <span className="text-[12px] font-medium leading-none text-mute">Label</span>
            <input
              type="text"
              placeholder="e.g. Deadlift 220 kg, Valencia Marathon, Hyrox Pro"
              value={form.label}
              onChange={(e) => set("label", e.target.value)}
              className={inputCls}
            />
          </label>

          <div className="grid grid-cols-2 gap-[14px]">
            <label className="block">
              <span className="text-[12px] font-medium leading-none text-mute">Domain</span>
              <select value={form.domain} onChange={(e) => set("domain", e.target.value)} className={inputCls} style={{ colorScheme: "dark" }}>
                {DOMAINS.map((d) => (
                  <option key={d.value} value={d.value}>{d.label}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-[12px] font-medium leading-none text-mute">Priority (1 highest)</span>
              <input type="number" min={1} max={5} value={form.priority} onChange={(e) => set("priority", e.target.value)} className={inputCls} />
            </label>
          </div>

          <div className="grid grid-cols-2 gap-[14px]">
            <label className="block">
              <span className="text-[12px] font-medium leading-none text-mute">Target value</span>
              <input type="number" placeholder="Optional" value={form.targetValue} onChange={(e) => set("targetValue", e.target.value)} className={inputCls} />
            </label>
            <label className="block">
              <span className="text-[12px] font-medium leading-none text-mute">Target unit</span>
              <input type="text" placeholder="kg, min, km…" value={form.targetUnit} onChange={(e) => set("targetUnit", e.target.value)} className={inputCls} />
            </label>
          </div>

          <label className="block">
            <span className="text-[12px] font-medium leading-none text-mute">Target date</span>
            <input type="date" value={form.targetDate} onChange={(e) => set("targetDate", e.target.value)} className={inputCls} style={{ colorScheme: "dark" }} />
          </label>

          <label className="block">
            <span className="text-[12px] font-medium leading-none text-mute">Benchmark code <span className="text-dim">(optional — links progress to a benchmark)</span></span>
            <input type="text" placeholder="Leave blank for a free-text objective" value={form.benchmarkCode} onChange={(e) => set("benchmarkCode", e.target.value)} className={inputCls} />
          </label>
        </div>

        <div className="flex items-center justify-between gap-[9px] border-t border-white/[0.06] px-6 py-4">
          <span className={cn("max-w-[300px] text-[11px] font-medium leading-[1.4]", saveError ? "text-hot" : "text-dim")}>
            {saveError ?? "Progress only computes when a benchmark code is linked; otherwise it's countdown-only."}
          </span>
          <div className="flex flex-none gap-[9px]">
            <button onClick={actions.closeObjectiveCreate} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft">Cancel</button>
            <button onClick={save} disabled={saving || !labelValid} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-[18px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60">
              {saving ? "Creating…" : auth.token ? "Create objective →" : "Sign in to create →"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
