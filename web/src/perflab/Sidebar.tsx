// src/perflab/Sidebar.tsx
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { usePerfLab } from "./store";
import type { Screen } from "./store";
import { Track } from "./ui";

const I = (d: ReactNode, sw = 1.7) => (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw}>
    {d}
  </svg>
);

const ICONS: Record<string, ReactNode> = {
  overview: I(
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </>,
  ),
  field: I(<path d="M3 12h4l3 8 4-16 3 8h4" />),
  twin: I(
    <>
      <path d="M12 2 4 7v10l8 5 8-5V7z" />
      <path d="M12 22V12M4 7l8 5 8-5" />
    </>,
    1.8,
  ),
  simulate: I(
    <>
      <path d="M3 17l5-5 4 4 9-9" />
      <path d="M21 7v5h-5" />
    </>,
  ),
  race: I(<path d="M4 21V4h13l-2 4 2 4H4" />),
  planning: I(
    <>
      <rect x="3" y="4" width="18" height="17" rx="2" />
      <path d="M3 9h18M8 2v4M16 2v4" />
    </>,
  ),
  history: I(
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 3" />
    </>,
  ),
  settings: I(
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </>,
  ),
  onboarding: I(
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c0-4 4-6 8-6s8 2 8 6" />
    </>,
  ),
};

const WORKSPACE: { screen: Screen; label: string }[] = [
  { screen: "overview", label: "Overview" },
  { screen: "field", label: "Field Test" },
  { screen: "twin", label: "Digital Twin" },
  { screen: "simulate", label: "Simulator" },
  { screen: "race", label: "Goal Race" },
  { screen: "planning", label: "Planning" },
  { screen: "history", label: "History" },
];
const SETUP: { screen: Screen; label: string }[] = [
  { screen: "settings", label: "Settings" },
  { screen: "onboarding", label: "Onboarding" },
];

function NavLink({ screen, label }: { screen: Screen; label: string }) {
  const { state, actions } = usePerfLab();
  const active = state.screen === screen;
  return (
    <a
      onClick={() => actions.setScreen(screen)}
      style={active ? { background: "var(--ac)", boxShadow: "0 6px 18px -8px var(--ac)" } : undefined}
      className={cn(
        "flex cursor-pointer items-center gap-[11px] rounded-[9px] px-[11px] py-[9px] text-[13.5px] leading-none no-underline",
        active ? "font-semibold text-[#0a0c10]" : "font-medium text-mute",
      )}
    >
      {ICONS[screen]}
      <span className="nav-label">{label}</span>
    </a>
  );
}

export function Sidebar() {
  const { state, actions } = usePerfLab();
  const collapsed = state.navCollapsed;
  return (
    <aside
      data-rail={collapsed ? "1" : "0"}
      style={{ width: collapsed ? "74px" : "248px" }}
      className="sticky top-0 flex h-screen flex-none flex-col gap-[26px] overflow-hidden border-r border-white/[0.06] bg-panel px-4 py-6"
    >
      <div className="brand-row flex items-center gap-[11px] px-2 py-1">
        <div className="brand-logo grid h-[30px] w-[30px] flex-none place-items-center rounded-[9px] bg-gradient-to-br from-ac to-teal font-sans text-[15px] font-extrabold leading-none text-[#0a0c10]">
          ◆
        </div>
        <div className="brand-text">
          <div className="text-[14px] font-bold leading-none tracking-[0.02em] text-ink">PERF LAB</div>
          <div className="mt-[3px] font-mono text-[10px] uppercase leading-[1.3] tracking-[0.14em] text-[#5f6672]">
            Performance OS
          </div>
        </div>
        <button
          onClick={actions.toggleNav}
          className="ml-auto h-[26px] w-[26px] flex-none rounded-[7px] border border-white/10 bg-white/[0.03] text-[13px] leading-none text-mute"
        >
          {collapsed ? "»" : "«"}
        </button>
      </div>

      <nav className="flex flex-col gap-[3px]">
        <div className="nav-section px-[10px] pb-[10px] pt-[6px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.18em] text-[#4e545f]">
          Workspace
        </div>
        {WORKSPACE.map((n) => (
          <NavLink key={n.screen} {...n} />
        ))}
        <div className="nav-section px-[10px] pb-[10px] pt-4 font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.18em] text-[#4e545f]">
          Setup
        </div>
        {SETUP.map((n) => (
          <NavLink key={n.screen} {...n} />
        ))}
      </nav>

      <div className="side-extra mt-auto flex flex-col gap-[14px]">
        <div className="rounded-[13px] border border-white/[0.07] bg-white/[0.02] p-[13px]">
          <div className="flex items-center justify-between">
            <span className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">
              Block
            </span>
            <span className="font-mono text-[11px] font-semibold leading-none text-ac">Mid-base</span>
          </div>
          <div className="mt-[10px]">
            <Track pct={42} background="linear-gradient(90deg,var(--ac),#7bd6c0)" className="h-[5px]" />
          </div>
          <div className="mt-2 text-[11px] font-medium leading-[1.3] text-faint">Week 3 of 7 · build phase</div>
        </div>
        <div className="flex items-center gap-[11px] px-1 py-[6px]">
          <div className="grid h-[34px] w-[34px] place-items-center rounded-full border border-white/10 bg-gradient-to-br from-[#2a3550] to-[#1a2030] text-[12px] font-bold leading-none text-soft">
            AR
          </div>
          <div className="leading-[1.3]">
            <div className="text-[13px] font-semibold text-[#e6e8ec]">A. Rivera</div>
            <div className="text-[11px] font-medium text-faint">Athlete · 28</div>
          </div>
        </div>
        <button
          onClick={actions.toggleFresh}
          className="rounded-[9px] border border-white/10 bg-white/[0.03] p-[9px] text-[11px] font-semibold leading-none text-mute"
        >
          {state.fresh ? "Exit empty-state preview" : "Preview empty state"}
        </button>
      </div>
    </aside>
  );
}
