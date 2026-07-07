// Series legend — the dependable identity channel, present for >= 2 series (the
// caller decides). Labels wear TEXT tokens, never the series color; identity comes
// from the colored key beside the text (a short line for lines, a rect for
// fills/bars) — so identity is never color-of-text alone.
import { cn } from "@/lib/utils";

export interface LegendItem {
  label: string;
  color: string;
  /** Key shape: "line" for line series, "rect" for area/bar. Default "rect". */
  mark?: "line" | "rect";
}

export function Legend({ items, className }: { items: readonly LegendItem[]; className?: string }) {
  if (!items.length) return null;
  return (
    <div className={cn("flex flex-wrap items-center gap-x-4 gap-y-1", className)}>
      {items.map((it) => (
        <span key={it.label} className="flex items-center gap-1.5 text-[11px] font-medium leading-none text-mute">
          {it.mark === "line" ? (
            <span className="h-[2px] w-3 flex-none rounded-full" style={{ background: it.color }} />
          ) : (
            <span className="h-2 w-2 flex-none rounded-[3px]" style={{ background: it.color }} />
          )}
          {it.label}
        </span>
      ))}
    </div>
  );
}
