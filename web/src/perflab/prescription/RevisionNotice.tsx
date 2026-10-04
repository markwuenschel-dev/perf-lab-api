// src/perflab/prescription/RevisionNotice.tsx
//
// Shown with today's session: says when the session was REPLACED (and why), and offers a
// re-check while a safety restriction is in force (todayRevision.ts holds the rules).
import { useState } from "react";
import type { PrescriptionRevisionRead } from "@/types";
import { canRecheck, revisionNotice } from "./todayRevision";

export function RevisionNotice({
  revision,
  onRecheck,
}: {
  revision: PrescriptionRevisionRead | null | undefined;
  onRecheck?: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const notice = revisionNotice(revision);
  const recheck = onRecheck && canRecheck(revision);
  if (!notice && !recheck) return null;
  return (
    <div data-testid="revision-notice" className="mt-3 flex flex-col gap-2 text-[12px] font-medium leading-[1.5] text-mute">
      {notice && <div>{notice}</div>}
      {recheck && (
        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={busy}
            className="text-[11.5px] font-semibold text-teal disabled:opacity-50"
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onRecheck();
              } catch {
                setError("Couldn't re-check right now — try again.");
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Re-checking…" : "Re-check today's session"}
          </button>
          <span className="text-dim">Lifts today's restriction if it has cleared.</span>
        </div>
      )}
      {error && <div role="alert">{error}</div>}
    </div>
  );
}
