// src/perflab/screens/twin/MiniTile.tsx
//
// The small stat tile used by both Digital Twin bodies — the live authed one
// (Habit, Struct. signal) and the guest sample preview (VO₂max, Profile, Habit,
// Struct. signal). It was declared inside TwinScreen while both bodies lived in
// that file; it moved here when the guest preview became its own module, so the
// two callers can share it without importing each other.
//
// Presentational only: every value, label and tooltip is supplied by the caller,
// so this component makes no claim about whether a number is live or sampled.

import type { ReactNode } from "react";
import { Card } from "../../ui";
import { Meter } from "../../viz";

export function MiniTile({ label, value, sub, foot, footColor, bar, tip }: { label: string; value: ReactNode; sub: string; foot?: string; footColor?: string; bar?: number; tip?: string }) {
  return (
    <Card className="flex flex-col justify-between p-[17px]">
      <div data-tip={tip} className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">{label}</div>
      <div className="my-3">
        <span className="font-mono text-[28px] font-semibold leading-none text-ink">{value}</span>
        <div className="mt-[5px] text-[10px] font-medium leading-none text-faint">{sub}</div>
      </div>
      {bar != null ? (
        <Meter variant="bare" pct={bar} color="var(--ac)" trackClassName="h-[5px]" />
      ) : (
        <div className={`font-mono text-[10px] leading-none ${footColor}`}>{foot}</div>
      )}
    </Card>
  );
}
