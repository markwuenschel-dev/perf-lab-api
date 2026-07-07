// src/perflab/ui.tsx
// Small presentational primitives shared across Perf Lab screens.
//
// ReadinessRing / MetricBar / Track are now thin SHIMS over the viz layer
// (Ring / Meter) so every call-site keeps working unchanged while the real
// implementation lives in one place. They are pixel-identical to the pre-viz
// versions and will be deleted once the last screen imports the viz primitives
// directly (per-screen migration phase).
import type { CSSProperties, ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Ring } from "./viz/Ring";
import { Meter } from "./viz/Meter";

export function Card({
  children,
  className,
  onClick,
  style,
  hover = true,
}: {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
  style?: CSSProperties;
  hover?: boolean;
}) {
  return (
    <div
      {...(hover ? { "data-card": "1" } : {})}
      onClick={onClick}
      style={style}
      className={cn(
        "rounded-[18px] border border-white/[0.06] bg-tile p-5",
        onClick && "cursor-pointer",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Tile({
  children,
  className,
  onClick,
  style,
}: {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
  style?: CSSProperties;
}) {
  return (
    <div
      data-tile="1"
      onClick={onClick}
      style={style}
      className={cn(
        "rounded-[14px] border border-white/[0.06] bg-tile p-4",
        onClick && "cursor-pointer",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Pill({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "rounded-[7px] border border-mint/25 bg-mint/[0.12] px-2 py-[5px] font-mono text-[10px] font-semibold leading-none tracking-[0.1em] text-[#9ad6c8]",
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Readiness donut: conic ring with a value in the middle. Shim over viz/Ring. */
export function ReadinessRing({
  value,
  color,
  size = 118,
  inner = 92,
  innerClassName = "bg-tile",
  valueClassName = "text-[34px]",
  onClick,
}: {
  value: number;
  color: string;
  size?: number;
  inner?: number;
  innerClassName?: string;
  valueClassName?: string;
  onClick?: () => void;
}) {
  return (
    <Ring value={value} color={color} size={size} inner={inner} innerClassName={innerClassName} onClick={onClick}>
      <span className={cn("font-mono font-semibold leading-none text-ink", valueClassName)}>{value}</span>
      <span className="mt-1 font-mono text-[9px] leading-none tracking-[0.14em] text-faint">/ 100</span>
    </Ring>
  );
}

/** Label · track · value row used for fatigue / dose / skill / axis lists. Shim over viz/Meter. */
export function MetricBar({
  label,
  value,
  pct,
  color,
  labelClassName = "w-[74px]",
  valueClassName = "w-[26px] text-soft",
  trackClassName = "h-[7px]",
  onClick,
  tip,
}: {
  label: ReactNode;
  value: ReactNode;
  pct: number;
  color: string;
  labelClassName?: string;
  valueClassName?: string;
  trackClassName?: string;
  onClick?: () => void;
  tip?: string;
}) {
  return (
    <Meter
      variant="row"
      pct={pct}
      color={color}
      label={label}
      value={value}
      labelClassName={labelClassName}
      valueClassName={valueClassName}
      trackClassName={trackClassName}
      onClick={onClick}
      tip={tip}
    />
  );
}

/** Standalone progress track. Shim over viz/Meter (bare). */
export function Track({
  pct,
  background = "var(--ac)",
  className = "h-[6px]",
}: {
  pct: number;
  background?: string;
  className?: string;
}) {
  return <Meter variant="bare" pct={pct} color={background} trackClassName={className} />;
}

export function ScreenHeader({
  title,
  badge,
  subtitle,
  children,
}: {
  title: string;
  badge?: ReactNode;
  subtitle?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="flex items-start justify-between gap-5">
      <div>
        <div className="flex items-center gap-[10px]">
          <h1 className="m-0 text-[25px] font-bold leading-none tracking-[-0.02em] text-ink">{title}</h1>
          {badge}
        </div>
        {subtitle && <p className="m-0 mt-[9px] max-w-[460px] text-[13.5px] font-medium leading-[1.5] text-mute">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-none items-center gap-[9px]">{children}</div>}
    </header>
  );
}

/** Pulsing "synced" status chip. */
export function SyncChip({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-[7px] rounded-[9px] border border-white/[0.08] px-3 py-2 text-[12px] font-medium leading-none text-[#8b919c]">
      <span className="h-[7px] w-[7px] animate-pl-pulse rounded-full bg-ac" />
      {label}
    </div>
  );
}
