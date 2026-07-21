// src/perflab/screens/twin/CapacityView.tsx
//
// The confidence-aware capacity presentation for the LIVE Digital Twin (BA-4).
// This component owns, in one place:
//   • the five-axis field mapping (X_t → the five plotted capacity axes),
//   • the canonical frontend ceilings (NOT the sim CAP_CFG maxes),
//   • per-axis established / provisional / insufficient rendering,
//   • the radar-vs-bars selection rule (BA-1),
//   • window-start comparability, and
//   • exclusion of INSUFFICIENT axes from every derived summary.
//
// Timeline orchestration, fatigue, tissue, habit, signal and current/historical
// metric selection stay in TwinScreen — this component is handed one resolved
// snapshot row plus the window-start row.
//
// BA-1 (radar vs bars):
//   • Per-axis displayable  = status is established OR provisional.
//   • ALL FIVE displayable  → full five-axis radar; provisional axes are plotted
//     but visibly marked as estimates (distinct label + legend note).
//   • ANY axis insufficient → drop the radar entirely; render the five-axis
//     bar/list. Insufficient rows show "Not enough evidence" with no numeric bar
//     and no window-start delta.
//   We NEVER render a radar over a subset of axes — a disappearing spoke would
//   move every other spoke and fake capacity change during a scrub.

import { Radar, type RadarAxis } from "../../viz";
import { SectionLabel } from "../../ui";
import type { CapacityState, StateHistorySnapshotRead } from "@/types";

// ---- Five plotted axes: X_t field mapping + canonical ceilings ----
// Ceilings are frontend presentation constants, deliberately independent of the
// sim's CAP_CFG maxes. Aerobic runs on a much larger scale than the others.
type CapKey = keyof CapacityState;
interface AxisDef {
  /** Confidence-status / CapacityState field name. */
  field: CapKey;
  /** Long label (bar list + axis table). */
  label: string;
  /** Short label (radar spoke). */
  short: string;
  /** Drawing ceiling (value / ceil → 0…1). Aerobic /650, all others /100. */
  ceil: number;
}
const AXES: readonly AxisDef[] = [
  { field: "aerobic", label: "Aerobic", short: "Aerobic", ceil: 650 },
  { field: "glycolytic", label: "Glycolytic", short: "Glyco", ceil: 100 },
  { field: "max_strength", label: "Max strength", short: "Strength", ceil: 100 },
  { field: "power", label: "Power", short: "Power", ceil: 100 },
  { field: "work_capacity", label: "Work cap", short: "Work", ceil: 100 },
];

const TYPE_NAMES: Record<string, string> = {
  aerobic: "Aerobic engine",
  glycolytic: "Glycolytic / speed",
  max_strength: "Strength-led",
  power: "Power-led",
  work_capacity: "Durability-led",
};

// The presentation policy owns the variance thresholds — we only narrow the
// string the backend already resolved. Any unknown → the safe "insufficient".
type ConfStatus = "established" | "provisional" | "insufficient";
function narrowStatus(s: string | undefined): ConfStatus {
  return s === "established" || s === "provisional" ? s : "insufficient";
}

const clamp01 = (n: number) => Math.max(0, Math.min(1, n));

interface AxisRow {
  field: CapKey;
  label: string;
  short: string;
  ceil: number;
  raw: number;
  startRaw: number;
  status: ConfStatus;
  displayable: boolean;
  provisional: boolean;
  /** Normalized (clamped) fraction — DRAWING only; the shown number is `raw`. */
  norm: number;
}

export interface CapacityViewProps {
  /** The resolved snapshot the twin is currently viewing. */
  row: StateHistorySnapshotRead;
  /** The window-start row (rows[0]) for the "vs window start" comparison. */
  startRow: StateHistorySnapshotRead;
  /** Suppress window-start deltas (THIN window: a single loaded vector). */
  showDelta: boolean;
}

export function CapacityView({ row, startRow, showDelta }: CapacityViewProps) {
  // capacity_x is guaranteed present at runtime for state-history rows (BA-3);
  // the schema marks it optional, so fall back defensively to keep types honest.
  const cap = row.capacity_x;
  const startCap = startRow.capacity_x;
  const statusMap = row.capacity_confidence_status ?? {};

  const axes: AxisRow[] = AXES.map((a) => {
    const raw = cap ? cap[a.field] : 0;
    const startRaw = startCap ? startCap[a.field] : raw;
    const status = narrowStatus(statusMap[a.field]);
    const displayable = status !== "insufficient";
    return {
      field: a.field,
      label: a.label,
      short: a.short,
      ceil: a.ceil,
      raw,
      startRaw,
      status,
      displayable,
      provisional: status === "provisional",
      norm: clamp01(raw / a.ceil),
    };
  });

  const displayableAxes = axes.filter((a) => a.displayable);
  const insufficientCount = axes.length - displayableAxes.length;
  const allDisplayable = insufficientCount === 0;

  return (
    <div className="rounded-[18px] border border-white/[0.06] bg-tile px-[22px] py-5">
      <div className="mb-[18px] flex items-center justify-between">
        <SectionLabel>Capacities · X(t)</SectionLabel>
        <div className="font-mono text-[10px] leading-none text-dim">
          {allDisplayable ? "5 axes · confidence-scored" : `${insufficientCount} of 5 need more evidence`}
        </div>
      </div>
      {allDisplayable ? (
        <RadarPanel axes={axes} displayableAxes={displayableAxes} showDelta={showDelta} />
      ) : (
        <BarsPanel axes={axes} displayableAxes={displayableAxes} insufficientCount={insufficientCount} showDelta={showDelta} />
      )}
    </div>
  );
}

// ---- Derived summary (dominant / type / composite / balance) ----
// Computed over the DISPLAYABLE axes only — INSUFFICIENT axes are excluded from
// every derived claim. Provisional axes ARE included (they are displayable).
function DerivedSummary({ axes, incompleteCount }: { axes: AxisRow[]; incompleteCount: number }) {
  if (axes.length === 0) {
    return (
      <div className="flex flex-col justify-center gap-[10px] self-stretch rounded-[14px] border border-white/[0.06] bg-white/[0.02] p-[18px]">
        <SectionLabel className="text-faint">Profile shape</SectionLabel>
        <div className="text-[13px] font-medium leading-[1.5] text-mute">
          Not enough evidence yet across the capacity axes — log training or a benchmark to establish them.
        </div>
      </div>
    );
  }

  const norms = axes.map((a) => a.norm);
  const dom = axes.reduce((best, a) => (a.norm > best.norm ? a : best), axes[0]);
  const composite = Math.round((norms.reduce((s, n) => s + n, 0) / norms.length) * 100);
  const minN = Math.min(...norms);
  const maxN = Math.max(...norms);
  const balPct = maxN > 0 ? Math.round((minN / maxN) * 100) : 0;
  const balanceWord =
    axes.length < 2 ? "Single axis" : balPct >= 80 ? "Well-rounded" : balPct >= 62 ? "Moderately specialised" : "Highly specialised";
  const profileNote =
    axes.length < 2
      ? "Only one axis has enough evidence to profile so far."
      : `Strongest in ${dom.label.toLowerCase()}. ${
          balPct >= 80
            ? "Capacities are evenly developed across the established axes."
            : "Development skews toward the leading axes — room to round out the lower ones."
        }`;

  return (
    <div className="flex flex-col justify-center gap-[14px] self-stretch rounded-[14px] border border-white/[0.06] bg-white/[0.02] p-[18px]">
      <div>
        <SectionLabel className="text-faint">Profile shape</SectionLabel>
        <div className="mt-[9px] text-[19px] font-bold leading-[1.1] text-ac">{TYPE_NAMES[dom.field] ?? dom.label}</div>
      </div>
      <div className="flex flex-col gap-[9px]">
        <SummaryRow k="Dominant axis" v={dom.label} />
        <SummaryRow k="Composite" v={`${composite}`} mono />
        <SummaryRow k="Balance" v={balanceWord} />
      </div>
      {incompleteCount > 0 && (
        <div className="rounded-[10px] border border-warn/25 bg-warn/[0.08] px-3 py-[9px] text-[11px] font-semibold leading-[1.4] text-warn">
          Capacity profile incomplete · {incompleteCount} of 5 axes need more evidence
        </div>
      )}
      <div className="border-t border-white/[0.06] pt-3 text-[11px] font-medium leading-[1.5] text-mute">{profileNote}</div>
    </div>
  );
}

// ---- Radar panel (all five axes displayable) ----
function RadarPanel({ axes, displayableAxes, showDelta }: { axes: AxisRow[]; displayableAxes: AxisRow[]; showDelta: boolean }) {
  const anyProvisional = axes.some((a) => a.provisional);
  // Provisional spokes are plotted but visibly marked: a "*" on the spoke label,
  // reinforced by the legend note and the "est" tag in the axis table.
  const radarAxes: RadarAxis[] = axes.map((a) => ({
    key: a.field,
    label: a.provisional ? `${a.short}*` : a.short,
    value: a.raw,
    max: a.ceil,
  }));
  const baseline = axes.map((a) => a.startRaw);

  return (
    <div className="grid grid-cols-1 items-center gap-[26px] lg:grid-cols-[280px_1fr_250px]">
      <div>
        <Radar axes={radarAxes} baseline={showDelta ? baseline : undefined} size={200} className="mx-auto block h-auto w-full max-w-[220px]" />
        <div className="mt-2 flex flex-wrap justify-center gap-x-[18px] gap-y-1 text-[10px] font-medium leading-none text-mute">
          <span><span className="mr-[5px] inline-block h-[3px] w-[12px] rounded-[2px] bg-ac align-middle" />now</span>
          {showDelta && (
            <span><span className="mr-[5px] inline-block w-[12px] border-t-[1.5px] border-dashed border-white/50 align-middle" />window start</span>
          )}
          {anyProvisional && <span className="text-warn">* provisional (estimate)</span>}
        </div>
      </div>
      <div className="flex flex-col gap-[13px]">
        <div className="flex items-center justify-between font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-dim">
          <span>Axis</span>
          {showDelta && <span>vs window start</span>}
        </div>
        {axes.map((a) => (
          <AxisBarRow key={a.field} axis={a} showDelta={showDelta} />
        ))}
      </div>
      <DerivedSummary axes={displayableAxes} incompleteCount={0} />
    </div>
  );
}

// ---- Bars panel (any axis insufficient → no radar) ----
function BarsPanel({
  axes,
  displayableAxes,
  insufficientCount,
  showDelta,
}: {
  axes: AxisRow[];
  displayableAxes: AxisRow[];
  insufficientCount: number;
  showDelta: boolean;
}) {
  return (
    <div className="grid grid-cols-1 items-start gap-[26px] lg:grid-cols-[1fr_250px]">
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 md:grid-cols-3">
        {axes.map((a) => (
          <AxisTile key={a.field} axis={a} showDelta={showDelta} />
        ))}
      </div>
      <DerivedSummary axes={displayableAxes} incompleteCount={insufficientCount} />
    </div>
  );
}

// A compact axis card for the bars view. Insufficient axes show an honest
// "Not enough evidence" with no bar and no delta.
function AxisTile({ axis, showDelta }: { axis: AxisRow; showDelta: boolean }) {
  if (!axis.displayable) {
    return (
      <div>
        <div className="mb-2 text-[12px] font-medium leading-none text-mute">{axis.label}</div>
        <div className="font-mono text-[15px] font-semibold leading-none text-dim">Not enough evidence</div>
        <div className="mb-[7px] mt-[11px] h-[6px] rounded-full border border-dashed border-white/[0.12]" />
        <div className="font-mono text-[10px] leading-none text-dim">measurement pending</div>
      </div>
    );
  }
  const delta = axis.raw - axis.startRaw;
  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-[12px] font-medium leading-none text-mute">
        {axis.label}
        {axis.provisional && <EstTag />}
      </div>
      <div className="font-mono text-[30px] font-semibold leading-none text-ink">{Math.round(axis.raw)}</div>
      <div className="mb-[7px] mt-[11px] h-[6px] overflow-hidden rounded-full bg-white/[0.07]">
        <div
          className="h-full rounded-full"
          style={{
            width: `${Math.max(4, axis.norm * 100)}%`,
            background: axis.provisional
              ? "repeating-linear-gradient(90deg,var(--ac) 0 6px,transparent 6px 10px)"
              : "linear-gradient(90deg,var(--ac),#a7e36e)",
          }}
        />
      </div>
      <div className="font-mono text-[10px] leading-none text-dim">
        {showDelta ? `${delta >= 0 ? "+" : ""}${Math.round(delta)} vs window start` : "current"}
      </div>
    </div>
  );
}

// The radar-mode axis table row. All five axes here are displayable.
function AxisBarRow({ axis, showDelta }: { axis: AxisRow; showDelta: boolean }) {
  const delta = axis.raw - axis.startRaw;
  return (
    <div className="flex items-center gap-3">
      <span className="flex w-[110px] flex-none items-center gap-1 text-[12px] font-medium leading-none text-mute">
        {axis.label}
        {axis.provisional && <EstTag />}
      </span>
      <span className="w-[46px] flex-none font-mono text-[16px] font-semibold leading-none text-ink">{Math.round(axis.raw)}</span>
      <div className="h-[6px] flex-1 overflow-hidden rounded-full bg-white/[0.07]">
        <div
          className="h-full rounded-full"
          style={{
            width: `${Math.max(4, axis.norm * 100)}%`,
            background: axis.provisional
              ? "repeating-linear-gradient(90deg,var(--ac) 0 6px,transparent 6px 10px)"
              : "linear-gradient(90deg,var(--ac),#a7e36e)",
          }}
        />
      </div>
      {showDelta && (
        <span className="w-[40px] text-right font-mono text-[11px] font-semibold leading-none text-teal">
          {delta >= 0 ? "+" : ""}
          {Math.round(delta)}
        </span>
      )}
    </div>
  );
}

function EstTag() {
  return (
    <span className="rounded-[4px] border border-warn/30 bg-warn/[0.1] px-[5px] py-[2px] font-mono text-[8.5px] font-semibold uppercase leading-none tracking-[0.08em] text-warn">
      est
    </span>
  );
}

function SummaryRow({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[11px] font-medium leading-none text-mute">{k}</span>
      <span className={`text-[12px] font-semibold leading-none text-ink ${mono ? "font-mono" : ""}`}>{v}</span>
    </div>
  );
}
