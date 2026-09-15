// src/perflab/InfoTip.tsx
//
// A small "i" help control that explains something beside it, for every kind of reader.
//
//   pointer   hovering opens it after a short delay; the content stays open while the pointer
//             moves from the trigger onto the content, and closes shortly after leaving both
//   tap/click toggles it open and keeps it open until dismissed
//   keyboard  a native <button>, so Enter/Space act as a click; focusing it also opens it, the
//             same way hovering does, and closes it on blur unless it was pinned by a click
//   assistive the same text is the trigger's accessible description (aria-describedby), so it
//             is announced on focus whether or not the popover is showing
//
// Dismissal never moves focus: an outside click or Escape closes it and focus stays where it is
// (on the trigger, when Escape is pressed from it). Help content is text only, so focus is never
// sent into the popover either.
import { useEffect, useId, useRef, useState } from "react";
import { Popover } from "radix-ui";

export interface InfoSection {
  heading?: string;
  text: string;
}

const OPEN_DELAY_MS = 120;
const CLOSE_DELAY_MS = 200;

export function InfoTip({ label, sections }: { label: string; sections: InfoSection[] }) {
  const [open, setOpen] = useState(false);
  const pinned = useRef(false);
  const timer = useRef<number | null>(null);
  const descriptionId = useId();

  const clearTimer = () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  };
  useEffect(() => clearTimer, []);

  const openSoon = () => {
    clearTimer();
    timer.current = window.setTimeout(() => setOpen(true), OPEN_DELAY_MS);
  };
  const closeSoon = () => {
    clearTimer();
    if (pinned.current) return;
    timer.current = window.setTimeout(() => setOpen(false), CLOSE_DELAY_MS);
  };

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        clearTimer();
        if (!next) pinned.current = false;
        setOpen(next);
      }}
    >
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label={label}
          aria-describedby={descriptionId}
          onClick={(e) => {
            // Handled here rather than by the trigger's own toggle, so a click on help already
            // opened by focus pins it instead of closing it.
            e.preventDefault();
            clearTimer();
            const next = !(open && pinned.current);
            pinned.current = next;
            setOpen(next);
          }}
          onPointerEnter={(e) => {
            if (e.pointerType !== "touch") openSoon();
          }}
          onPointerLeave={(e) => {
            if (e.pointerType !== "touch") closeSoon();
          }}
          onFocus={() => {
            clearTimer();
            setOpen(true);
          }}
          onBlur={() => {
            if (!pinned.current) {
              clearTimer();
              setOpen(false);
            }
          }}
          className="inline-flex h-[15px] w-[15px] flex-none items-center justify-center rounded-full border border-white/20 font-mono text-[9.5px] font-semibold leading-none text-dim hover:border-white/40 hover:text-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ac"
        >
          i
        </button>
      </Popover.Trigger>
      <span id={descriptionId} className="sr-only">
        {sections.map((s) => (s.heading ? `${s.heading}: ${s.text}` : s.text)).join(" ")}
      </span>
      <Popover.Portal>
        <Popover.Content
          side="top"
          align="start"
          sideOffset={6}
          collisionPadding={12}
          onOpenAutoFocus={(e) => e.preventDefault()}
          onCloseAutoFocus={(e) => e.preventDefault()}
          onPointerEnter={clearTimer}
          onPointerLeave={(e) => {
            if (e.pointerType !== "touch") closeSoon();
          }}
          className="z-[90] flex max-w-[280px] flex-col gap-2 rounded-[10px] border border-white/[0.14] bg-[#1b212b] px-3 py-[10px] text-[11.5px] font-medium leading-[1.5] text-[#cfd4dd] shadow-lg"
        >
          {sections.map((s, i) => (
            <div key={i}>
              {s.heading && (
                <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-dim">{s.heading}</div>
              )}
              <div>{s.text}</div>
            </div>
          ))}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
