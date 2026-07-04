// src/perflab/overlays/MacrocycleCreateModal.tsx
//
// New-macrocycle overlay (POST /v1/macrocycles). A macrocycle is a thin
// "program" container above training blocks, anchored to an Objective, that
// yields a real cross-block "week X of Y". The form picks an anchor objective
// (the dropdown shows objective labels; objective_id is the value) plus an
// optional start_date (defaults server-side to today when left blank).
import { useState } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/auth/useAuth";
import { createMacrocycle, listObjectives } from "@/api/perfLabClient";
import type { ApiError, MacrocycleCreate, ObjectiveRead } from "@/types";
import { usePerfLab } from "../store";
import { useAuthedResource } from "../useAuthedResource";
import { sortObjectives } from "../objectives";
import { CloseBtn } from "./LogWorkoutModal";

const inputCls = "mt-2 w-full rounded-[11px] border border-white/10 bg-panel px-[13px] py-[11px] text-[14px] text-ink";

export function MacrocycleCreateModal() {
  const { state, actions } = usePerfLab();
  const auth = useAuth();
  const [objectiveId, setObjectiveId] = useState<string>("");
  const [startDate, setStartDate] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Anchor-objective picker: only the athlete's active objectives can anchor a
  // new program. Re-fetches when the objectives list changes.
  const { data: objectives, loading } = useAuthedResource<ObjectiveRead[]>(
    (t) => listObjectives(t, "active"),
    [state.objectivesRefreshKey, state.macrocycleCreateOpen],
  );

  if (!state.macrocycleCreateOpen) return null;

  const options = objectives ? sortObjectives(objectives) : [];
  const idValid = objectiveId.trim() !== "" && !Number.isNaN(Number(objectiveId));

  // Submit → POST /v1/macrocycles (auth required); on success, bump the refresh
  // key so the Overview header + Objectives' Program section re-fetch.
  async function save() {
    if (!auth.token) {
      actions.closeMacrocycleCreate();
      actions.openAuth();
      return;
    }
    if (!idValid) {
      setSaveError("Pick an anchor objective for the program.");
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const req: MacrocycleCreate = {
        objective_id: Number(objectiveId),
        start_date: startDate === "" ? null : startDate,
      };
      await createMacrocycle(req, auth.token);
      actions.refreshMacrocycles();
      actions.closeMacrocycleCreate();
      setObjectiveId("");
      setStartDate("");
    } catch (e) {
      setSaveError(
        (e as ApiError)?.message ??
          "Couldn't create the program — check you're signed in and the backend is reachable.",
      );
    } finally {
      setSaving(false);
    }
  }

  const noObjectives = !loading && objectives !== null && options.length === 0;

  return (
    <div className="fixed inset-0 z-[64] flex items-center justify-center p-8 backdrop-blur-[4px]" style={{ background: "rgba(4,5,8,.7)" }}>
      <div className="max-h-[92vh] w-[560px] max-w-full overflow-auto rounded-[18px] border border-white/[0.09] bg-surface shadow-[0_50px_110px_-30px_rgba(0,0,0,.75)]">
        <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-5">
          <div className="flex items-center gap-[10px]">
            <h2 className="m-0 text-[18px] font-bold leading-none tracking-[-0.01em] text-ink">New program</h2>
            <span className="rounded-[7px] border border-mint/25 bg-mint/[0.12] px-2 py-[5px] font-mono text-[10px] font-semibold leading-none tracking-[0.1em] text-[#9ad6c8]">macrocycle</span>
          </div>
          <CloseBtn onClick={actions.closeMacrocycleCreate} />
        </div>

        <div className="flex flex-col gap-[18px] px-6 py-[22px]">
          <label className="block">
            <span className="text-[12px] font-medium leading-none text-mute">Anchor objective</span>
            <select
              value={objectiveId}
              onChange={(e) => setObjectiveId(e.target.value)}
              disabled={noObjectives}
              className={inputCls}
              style={{ colorScheme: "dark" }}
            >
              <option value="">{loading ? "Loading objectives…" : noObjectives ? "No active objectives" : "Select an objective…"}</option>
              {options.map((o) => (
                <option key={o.id} value={String(o.id)}>{o.label}</option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-[12px] font-medium leading-none text-mute">Start date <span className="text-dim">(optional — defaults to today)</span></span>
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className={inputCls} style={{ colorScheme: "dark" }} />
          </label>

          {noObjectives && (
            <div className="rounded-[11px] border border-white/10 bg-white/[0.03] px-[13px] py-[11px] text-[12px] font-medium leading-[1.5] text-mute">
              A program needs an objective to aim at. Create an objective first, then anchor a program to it.
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-[9px] border-t border-white/[0.06] px-6 py-4">
          <span className={cn("max-w-[300px] text-[11px] font-medium leading-[1.4]", saveError ? "text-hot" : "text-dim")}>
            {saveError ?? "The program's week X of Y counts across all your blocks toward the objective."}
          </span>
          <div className="flex flex-none gap-[9px]">
            <button onClick={actions.closeMacrocycleCreate} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft">Cancel</button>
            <button onClick={save} disabled={saving || !idValid} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-[18px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60">
              {saving ? "Creating…" : auth.token ? "Create program →" : "Sign in to create →"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
