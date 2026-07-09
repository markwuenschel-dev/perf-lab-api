// src/perflab/overlays/SetBuilder.tsx
//
// Catalog-bound, per-set workout entry (ADR-0045). The set is the atomic unit and
// it binds to a catalog Exercise; the exercise's `load_type` types which fields are
// shown. A `count` acts as a quick-entry multiplier — 3×5 @ 100 kg is one row that
// the backend materializes into three set rows. Modality is derived from the mix.
import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { listExercises } from "@/api/perfLabClient";
import type { ExerciseCatalogOut } from "@/types";
import { blankGroup, LOADED, type SetGroup, topSetKeys } from "./setBuilderLogic";

const numField =
  "w-full rounded-[9px] border border-white/10 bg-panel px-[10px] py-[8px] font-mono text-[13px] text-ink";
const miniLabel = "text-[10px] font-medium leading-none text-dim";

export function SetBuilder({
  groups,
  onChange,
}: {
  groups: SetGroup[];
  onChange: (g: SetGroup[]) => void;
}) {
  const topKeys = useMemo(() => topSetKeys(groups), [groups]);

  const set = (key: number, patch: Partial<SetGroup>) =>
    onChange(groups.map((g) => (g.key === key ? { ...g, ...patch } : g)));
  const remove = (key: number) => onChange(groups.filter((g) => g.key !== key));
  const add = () => onChange([...groups, blankGroup()]);

  return (
    <div className="flex flex-col gap-[10px]">
      {groups.map((g) => (
        <GroupCard
          key={g.key}
          group={g}
          isTop={topKeys.has(g.key)}
          onPatch={(p) => set(g.key, p)}
          onRemove={() => remove(g.key)}
        />
      ))}
      <button
        onClick={add}
        className="rounded-[9px] border border-dashed border-white/15 bg-white/[0.02] px-3 py-[10px] text-[12px] font-semibold text-mute hover:border-ac/40 hover:text-ac"
      >
        + Add exercise
      </button>
    </div>
  );
}

function GroupCard({
  group,
  isTop,
  onPatch,
  onRemove,
}: {
  group: SetGroup;
  isTop: boolean;
  onPatch: (p: Partial<SetGroup>) => void;
  onRemove: () => void;
}) {
  const lt = group.loadType;
  const num = (set: (n: number | undefined) => void) => (v: string) => {
    const n = parseFloat(v);
    set(isNaN(n) ? undefined : n);
  };

  return (
    <div className="rounded-[12px] border border-white/[0.08] bg-panel/60 p-3">
      <div className="mb-[10px] flex items-center gap-2">
        <ExercisePicker
          exercise={group.exercise}
          freeText={group.freeText}
          onPick={(ex) => onPatch({ exercise: ex, loadType: ex.load_type })}
          onFreeText={(t) => onPatch({ exercise: null, freeText: t })}
        />
        {isTop && (
          <span className="rounded-[6px] border border-ac/30 bg-ac/[0.12] px-[7px] py-[4px] font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.08em] text-ac">
            top set
          </span>
        )}
        <button
          onClick={onRemove}
          className="ml-auto h-[26px] w-[26px] rounded-[7px] border border-white/10 bg-white/[0.03] text-[12px] leading-none text-dim hover:text-hot"
          aria-label="Remove exercise"
        >
          ✕
        </button>
      </div>

      <div className="flex flex-wrap items-end gap-[10px]">
        {/* Sets multiplier — always meaningful */}
        <Field label="Sets" className="w-[58px]">
          <input type="number" min={1} value={group.count}
            onChange={(e) => onPatch({ count: Math.max(1, parseInt(e.target.value) || 1) })}
            className={numField} />
        </Field>

        {LOADED.has(lt) && (
          <>
            <Field label="Reps" className="w-[58px]">
              <input type="number" min={0} defaultValue={group.reps} onChange={(e) => num((n) => onPatch({ reps: n }))(e.target.value)} className={numField} />
            </Field>
            <Field label="Load (kg)" className="w-[78px]">
              <input type="number" min={0} step={2.5} value={group.loadKg ?? ""} onChange={(e) => num((n) => onPatch({ loadKg: n }))(e.target.value)} className={numField} />
            </Field>
          </>
        )}

        {lt === "bodyweight" && (
          <>
            <Field label="Reps" className="w-[58px]">
              <input type="number" min={0} defaultValue={group.reps} onChange={(e) => num((n) => onPatch({ reps: n }))(e.target.value)} className={numField} />
            </Field>
            <Field label="Band" className="w-[76px]">
              <input defaultValue={group.band} onChange={(e) => onPatch({ band: e.target.value })} className={numField} />
            </Field>
          </>
        )}

        {lt === "distance" && (
          <>
            <Field label="Distance (m)" className="w-[92px]">
              <input type="number" min={0} defaultValue={group.distanceM} onChange={(e) => num((n) => onPatch({ distanceM: n }))(e.target.value)} className={numField} />
            </Field>
            <Field label="Time (s)" className="w-[76px]">
              <input type="number" min={0} defaultValue={group.durationS} onChange={(e) => num((n) => onPatch({ durationS: n }))(e.target.value)} className={numField} />
            </Field>
          </>
        )}

        {lt === "time" && (
          <Field label="Time (s)" className="w-[76px]">
            <input type="number" min={0} defaultValue={group.durationS} onChange={(e) => num((n) => onPatch({ durationS: n }))(e.target.value)} className={numField} />
          </Field>
        )}

        <Field label="RPE" className="w-[58px]">
          <input type="number" min={1} max={10} step={0.5} defaultValue={group.rpe} onChange={(e) => num((n) => onPatch({ rpe: n }))(e.target.value)} className={numField} />
        </Field>
      </div>
    </div>
  );
}

function Field({ label, className, children }: { label: string; className?: string; children: React.ReactNode }) {
  return (
    <label className={cn("flex flex-col gap-[5px]", className)}>
      <span className={miniLabel}>{label}</span>
      {children}
    </label>
  );
}

/** Debounced catalog search; falls back to a free-text movement when nothing matches. */
function ExercisePicker({
  exercise,
  freeText,
  onPick,
  onFreeText,
}: {
  exercise: ExerciseCatalogOut | null;
  freeText: string;
  onPick: (ex: ExerciseCatalogOut) => void;
  onFreeText: (t: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ExerciseCatalogOut[]>([]);
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const id = window.setTimeout(() => {
      const term = query.trim();
      if (!open || term.length < 1) {
        setResults([]);
        return;
      }
      listExercises({ q: term })
        .then((r) => !cancelled && setResults(r.slice(0, 8)))
        .catch(() => !cancelled && setResults([]));
    }, 220);
    return () => {
      cancelled = true;
      window.clearTimeout(id);
    };
  }, [query, open]);

  const label = exercise?.name ?? freeText;

  return (
    <div ref={boxRef} className="relative flex-1">
      <input
        value={open ? query : label}
        placeholder="Search exercises…"
        onFocus={() => {
          setOpen(true);
          setQuery("");
        }}
        onBlur={() => window.setTimeout(() => setOpen(false), 160)}
        onChange={(e) => {
          setQuery(e.target.value);
          onFreeText(e.target.value); // keep as free-text until a catalog pick
        }}
        className="w-full rounded-[9px] border border-white/10 bg-panel px-[11px] py-[8px] text-[13px] font-semibold text-ink"
      />
      {open && results.length > 0 && (
        <div className="absolute left-0 top-[calc(100%+4px)] z-20 max-h-[220px] w-full overflow-auto rounded-[10px] border border-white/10 bg-surface shadow-[0_20px_50px_-20px_rgba(0,0,0,.8)]">
          {results.map((ex) => (
            <button
              key={ex.id}
              onMouseDown={(e) => {
                e.preventDefault();
                onPick(ex);
                setOpen(false);
              }}
              className="flex w-full items-center justify-between gap-2 px-3 py-[9px] text-left text-[12.5px] text-soft hover:bg-white/[0.05]"
            >
              <span className="font-semibold">{ex.name}</span>
              <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-dim">{ex.load_type}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
