// The hover readout. Values lead (Strong, tabular), the series name follows —
// the legend's hierarchy inverted, because here the reader has the series and
// wants the number. Series are keyed by a short line stroke, not a filled box.
// Labels arrive as React children, so they are auto-escaped (never innerHTML).
export interface TooltipRow {
  label: string;
  value: string;
  color: string;
}

export function Tooltip({ leftPct, title, rows }: { leftPct: number; title?: string; rows: readonly TooltipRow[] }) {
  // Keep the box inside the plot: flip the transform near the edges.
  const anchor = leftPct > 78 ? "translateX(-100%)" : leftPct < 22 ? "translateX(0)" : "translateX(-50%)";
  return (
    <div className="pointer-events-none absolute top-1 z-10" style={{ left: `${leftPct}%`, transform: anchor }}>
      <div className="rounded-[9px] border border-white/[0.14] bg-[#1b212b] px-2.5 py-2 shadow-[0_14px_34px_-12px_rgba(0,0,0,.75)]">
        {title && <div className="mb-1 whitespace-nowrap text-[10px] font-semibold leading-none text-mute">{title}</div>}
        {rows.map((r, i) => (
          <div key={i} className="flex items-center gap-2 whitespace-nowrap py-[1px] text-[11px] leading-none">
            <span className="h-[2px] w-2.5 flex-none rounded-full" style={{ background: r.color }} />
            <span className="font-mono font-semibold text-ink" style={{ fontVariantNumeric: "tabular-nums" }}>{r.value}</span>
            <span className="text-faint">{r.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
