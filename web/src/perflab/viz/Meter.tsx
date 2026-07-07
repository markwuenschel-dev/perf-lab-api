// Horizontal meter — the canonical label·track·value row (fatigue / dose / skill
// lists) and the bare progress track. The unfilled track is theme-aware. Band and
// diverging variants (ACWR sweet-spot, speed↔endurance) arrive in Phase 3.
import type { CSSProperties, ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useVizTheme } from "./useVizTheme";

export interface MeterProps {
  /** Fill percentage 0–100. */
  pct: number;
  /** Fill color (row) / background (bare). Defaults to the accent. */
  color?: string;
  variant?: "row" | "bare";
  /** Row: leading label. */
  label?: ReactNode;
  /** Row: trailing value. */
  value?: ReactNode;
  /** Override the unfilled track color (defaults to the theme track). */
  track?: string;
  className?: string;
  labelClassName?: string;
  valueClassName?: string;
  trackClassName?: string;
  onClick?: () => void;
  tip?: string;
  style?: CSSProperties;
}

export function Meter({
  pct,
  color,
  variant = "row",
  label,
  value,
  track,
  className,
  labelClassName = "w-[74px]",
  valueClassName = "w-[26px] text-soft",
  trackClassName,
  onClick,
  tip,
  style,
}: MeterProps) {
  const { chrome, accent } = useVizTheme();
  const width = `${Math.max(0, Math.min(100, pct))}%`;
  const trackBg = track ?? chrome.track;
  const fill = color ?? accent;

  if (variant === "bare") {
    return (
      <div
        className={cn("overflow-hidden rounded-full", trackClassName ?? "h-[6px]", className)}
        style={{ background: trackBg, ...style }}
      >
        <div className="h-full rounded-full" style={{ width, background: fill }} />
      </div>
    );
  }

  return (
    <div onClick={onClick} style={style} className={cn("flex items-center gap-3", onClick && "cursor-pointer", className)}>
      <span data-tip={tip} className={cn("flex-none text-[12px] font-medium leading-none text-mute", labelClassName)}>
        {label}
      </span>
      <div className={cn("flex-1 overflow-hidden rounded-full", trackClassName ?? "h-[7px]")} style={{ background: trackBg }}>
        <div className="h-full rounded-full" style={{ width, background: fill }} />
      </div>
      <span className={cn("text-right font-mono text-[12px] font-semibold leading-none", valueClassName)}>{value}</span>
    </div>
  );
}
