// Figure-as-a-number: label · value · optional delta / sub / sparkline. Replaces
// the ad-hoc MiniTile / Snap / StatCol / KPI blocks. The value uses tabular-nums;
// `hero` bumps it to >= 48px for the ONE headline number a view leads with.
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useVizTheme } from "./useVizTheme";
import { Sparkline } from "./Sparkline";

export interface StatTileProps {
  label: ReactNode;
  value: ReactNode;
  /** Secondary line under the value. */
  sub?: ReactNode;
  /** Signed change vs a named period; color follows direction × whether up is good. */
  delta?: { text: ReactNode; good?: boolean };
  /** Optional trend sparkline. */
  spark?: readonly number[];
  sparkColor?: string;
  /** Headline number (>= 48px). Exactly one per view. */
  hero?: boolean;
  className?: string;
  valueClassName?: string;
}

export function StatTile({ label, value, sub, delta, spark, sparkColor, hero, className, valueClassName }: StatTileProps) {
  const { colors } = useVizTheme();
  const deltaColor = delta ? (delta.good === false ? colors.status.critical : colors.status.good) : undefined;
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <span className="text-[11px] font-medium uppercase leading-none tracking-[0.1em] text-mute">{label}</span>
      <div className="flex items-baseline gap-2">
        <span
          className={cn("font-semibold leading-none text-ink", hero ? "text-[48px]" : "text-[22px]", valueClassName)}
          style={{ fontVariantNumeric: "tabular-nums" }}
        >
          {value}
        </span>
        {delta && (
          <span className="font-mono text-[12px] font-semibold leading-none" style={{ color: deltaColor }}>
            {delta.text}
          </span>
        )}
      </div>
      {sub && <span className="text-[12px] leading-none text-soft">{sub}</span>}
      {spark && spark.length > 1 && <Sparkline values={spark} color={sparkColor} width={120} height={28} className="mt-1 w-full" />}
    </div>
  );
}
