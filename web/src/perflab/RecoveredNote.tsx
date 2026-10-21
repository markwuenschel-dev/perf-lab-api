// src/perflab/RecoveredNote.tsx
//
// The visible caveat on a recovered training-state value (INT-15 2C). One component so every
// surface says the same thing in the same weight; the copy lives in provenance.ts.
import { cn } from "@/lib/utils";
import { RECOVERED_WARNING } from "./provenance";

export function RecoveredNote({ children, className }: { children?: string | null; className?: string }) {
  return (
    <div
      role="note"
      data-testid="recovered-note"
      className={cn(
        "rounded-[10px] border border-warn/25 bg-warn/[0.08] px-3 py-[8px] text-[11px] font-semibold leading-[1.4] text-warn",
        className,
      )}
    >
      {children ?? RECOVERED_WARNING}
    </div>
  );
}
