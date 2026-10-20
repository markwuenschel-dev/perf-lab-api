// src/perflab/sidebar/AuthedSidebarBlock.tsx
//
// The AUTHENTICATED sidebar block card. Reads the real macrocycle resource and
// renders real-or-honest-absence — never a literal.
//
// This module is an authenticated authority-bearing surface and is listed in
// overviewBoundary.test.ts's AUTHED_ROOTS: it must never reach a fixture module.
import { usePerfLab } from "../store";
import { useAuthedResource } from "../useAuthedResource";
import { Track } from "../ui";
import { listMacrocycles } from "@/api/perfLabClient";
import type { MacrocycleRead } from "@/types";
import { sidebarBlockView } from "./sidebarBlockModel";

const CARD = "rounded-[13px] border border-white/[0.07] bg-white/[0.02] p-[13px]";
const HEADER =
  "font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint";

/** The card chrome, shared by every branch so the sidebar never jumps height. */
function BlockCard({ right, children }: { right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className={CARD}>
      <div className="flex items-center justify-between gap-2">
        <span className={HEADER}>Block</span>
        {right}
      </div>
      {children}
    </div>
  );
}

const fetchMacrocycles = (token: string): Promise<MacrocycleRead[]> => listMacrocycles(token);

export function AuthedSidebarBlock() {
  const { state, actions } = usePerfLab();
  // The sidebar lives outside the screen swap (AppShell.tsx), so it never unmounts:
  // without the refresh key in the deps, a program created mid-session would leave
  // this card claiming "No active block" until a hard reload.
  const resource = useAuthedResource<MacrocycleRead[]>(fetchMacrocycles, [state.macrocyclesRefreshKey], { calendar: true });
  const view = sidebarBlockView(resource);

  switch (view.kind) {
    // No request authority at all — the guest branch renders GuestSidebarBlock.
    case "guest":
      return null;

    // A shell: no values, and crucially not an empty state, so a slow first load
    // never flashes "No active block" at an athlete who has one.
    case "loading":
      return (
        <BlockCard>
          <div className="mt-[10px] h-[5px] w-full animate-pulse rounded-full bg-white/[0.06]" />
          <div className="mt-2 h-[11px] w-2/3 animate-pulse rounded bg-white/[0.05]" />
        </BlockCard>
      );

    // The request failed outright. Distinct from "none" — we do not know whether
    // a block exists, so we must not claim there isn't one.
    case "unavailable":
      return (
        <BlockCard>
          <div className="mt-2 text-[11px] font-medium leading-[1.3] text-faint">
            Block unavailable
          </div>
        </BlockCard>
      );

    // Succeeded, and the athlete genuinely has no active macrocycle.
    case "none":
      return (
        <BlockCard>
          <div className="mt-2 text-[11px] font-medium leading-[1.3] text-faint">
            No active block
          </div>
          <button
            onClick={() => actions.setScreen("planning")}
            className="mt-[9px] w-full rounded-[8px] border border-white/10 bg-white/[0.03] p-[7px] text-[11px] font-semibold leading-none text-mute"
          >
            Create plan
          </button>
        </BlockCard>
      );

    case "block":
      return (
        <BlockCard
          right={
            <span className="truncate font-mono text-[11px] font-semibold leading-none text-ac">
              {view.label}
            </span>
          }
        >
          {/* Bar only when the backend gave a schedule position. An open-horizon
              program sends pct: null, and no percentage is invented for it. */}
          {view.pct != null && (
            <div className="mt-[10px]">
              <Track
                pct={view.pct}
                background="linear-gradient(90deg,var(--ac),#7bd6c0)"
                className="h-[5px]"
              />
            </div>
          )}
          <div className="mt-2 text-[11px] font-medium leading-[1.3] text-faint">
            {view.weekLine}
          </div>
          {view.stale && (
            <div className="mt-[6px] text-[10px] font-medium leading-[1.3] text-amber-400/80">
              Couldn't refresh — showing last known
            </div>
          )}
        </BlockCard>
      );
  }
}
