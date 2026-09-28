// src/perflab/screens/overview/overviewLeaves.tsx
//
// Presentational leaves shared by the guest preview and the authenticated body.
//
// EVERY component here is provenance-NEUTRAL: it takes a resolved value as a prop
// and makes no claim about where that value came from. That is the whole condition
// under which sharing is safe (map #182, #185): the two branches may share leaves,
// but never selectors, fallback logic, or a combined view model — those are exactly
// the places a fixture could re-enter the authenticated path.
//
// Nothing in this file may import sim.ts or read the store.
import type { ReactNode } from "react";
import { Ring } from "../../viz";
import { dateLine, greetingPrefix } from "./overviewClock";
import type { MetricState } from "./overviewModel";

/** The one place "we don't have this" is rendered, so it is impossible to miss. */
export const EM_DASH = "—";

/**
 * Render a metric state as text.
 *
 * There is deliberately no `fallback` prop. A caller that could pass a substitute
 * value is a caller that could pass a fabricated one, and the whole point of
 * `MetricState` is that the absent case has no substitute.
 */
export function MetricText({ state }: { state: MetricState<string | number> }) {
  if (state.kind === "value") return <>{state.value}</>;
  if (state.kind === "loading") return <span className="text-dim">…</span>;
  return <span className="text-dim">{EM_DASH}</span>;
}

export function StatCol({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.1em] text-faint">{label}</div>
      <div className="mt-[7px] font-mono text-[15px] font-semibold leading-none text-ink">{children}</div>
    </div>
  );
}

export function Snap({ label, value, color = "text-ink" }: { label: string; value: ReactNode; color?: string }) {
  return (
    <div>
      <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.1em] text-faint">{label}</div>
      <div className={`mt-[7px] font-mono text-[16px] font-semibold leading-none ${color}`}>{value}</div>
    </div>
  );
}

/** A greyed, non-scored readiness ring for "no number to show": the viz Ring at
 *  zero, so the whole circle is the theme track and no arc implies a value. */
export function NeutralRing({ size = 96, inner = 74 }: { size?: number; inner?: number }) {
  return (
    <Ring value={0} color="transparent" size={size} inner={inner}>
      <span className="font-mono text-[26px] font-semibold leading-none text-dim">{EM_DASH}</span>
    </Ring>
  );
}

/** A short explanatory line under a section that has nothing to show. */
export function EmptyLine({ children }: { children: ReactNode }) {
  return <div className="py-[11px] text-[12px] font-medium leading-[1.5] text-dim">{children}</div>;
}

/** The screen header shell. Purely structural — every piece is passed in. */
export function OverviewHeader({
  name,
  subtitleExtra,
  actions,
}: {
  name: string;
  subtitleExtra?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-5">
      <div>
        <h1 className="m-0 text-[25px] font-bold leading-none tracking-[-0.02em] text-ink">
          {greetingPrefix()}, {name}
        </h1>
        <p className="m-0 mt-[9px] text-[13.5px] font-medium leading-[1.5] text-mute">
          {dateLine()}
          {subtitleExtra}
        </p>
      </div>
      {actions && <div className="flex flex-none items-center gap-[9px]">{actions}</div>}
    </header>
  );
}
