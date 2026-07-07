// Conic donut gauge — the canonical ring (readiness, HR gauge, live check-in).
// The unfilled track is theme-aware (chrome.track) rather than a hard-coded
// white-alpha, so it reads correctly in both modes. Center content is a slot.
import type { CSSProperties, ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useVizTheme } from "./useVizTheme";

export interface RingProps {
  /** Current value (0…max). */
  value: number;
  max?: number;
  /** Arc color. Readiness passes a status color; gauges pass the accent. */
  color: string;
  /** Outer diameter in px. Default 118. */
  size?: number;
  /** Inner punch-out diameter in px. Default 92 (→ 13px ring). */
  inner?: number;
  /** Override the unfilled track color (defaults to the theme track). */
  track?: string;
  className?: string;
  innerClassName?: string;
  style?: CSSProperties;
  onClick?: () => void;
  /** Center content (value + unit, etc.). */
  children?: ReactNode;
}

export function Ring({
  value,
  max = 100,
  color,
  size = 118,
  inner = 92,
  track,
  className,
  innerClassName = "bg-tile",
  style,
  onClick,
  children,
}: RingProps) {
  const { chrome } = useVizTheme();
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const trackColor = track ?? chrome.track;
  return (
    <div
      onClick={onClick}
      style={{ width: size, height: size, background: `conic-gradient(${color} 0 ${pct}%, ${trackColor} ${pct}% 100%)`, ...style }}
      className={cn("grid flex-none place-items-center rounded-full", onClick && "cursor-pointer", className)}
    >
      <div
        style={{ width: inner, height: inner }}
        className={cn("flex flex-col items-center justify-center rounded-full", innerClassName)}
      >
        {children}
      </div>
    </div>
  );
}
