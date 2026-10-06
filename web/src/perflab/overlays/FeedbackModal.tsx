// src/perflab/overlays/FeedbackModal.tsx
//
// THE FEEDBACK BOUNDARY.
//
// Two overlays live in this file because they answer to different truths.
//
// `AuthedFeedbackForm` reports a real outcome for a real planned session. Every
// field it shows is a field the backend stores, and its success screen claims only
// what actually happened — a row was recorded, and it will inform the next
// prescription. It never says the twin advanced: `POST /v1/feedback` writes a
// label, not state, and it never touches `PlannedSession.status` (ADR-0070).
//
// `GuestFeedbackPreview` is the unauthenticated demo. Its numbers come from
// `sim`, and it is reachable only from `SessionPlayer`, which is itself guest-only.
// An authenticated athlete must NEVER reach it — previously they could, and were
// shown invented distance/pace/HR plus a "Twin updated" screen backed by nothing.
// The refusal lives at the boundary below rather than at the call site, so it
// holds regardless of who opens the overlay.
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/auth/useAuth";
import { createSessionFeedback } from "@/api/perfLabClient";
import type { ApiError, SessionFeedbackIn, SessionFeedbackOut, SessionStatus } from "@/types";
import { usePerfLab } from "../store";
import { outcomesFor, type Outcome } from "../sessionActions";
import type { Feel } from "../store";
import { COLORS, projectLogDose } from "../sim";
import { CloseBtn } from "./LogWorkoutModal";

export function FeedbackModal() {
  const { state } = usePerfLab();
  const { token } = useAuth();

  if (!state.feedbackOpen) return null;

  // A real session id is the only thing that makes this overlay able to write.
  if (state.feedbackSessionId != null) {
    // Keyed by session AND status: a draft belongs to one session's outcome. Opening another
    // session (or the same one after its status changed) must start a fresh draft, never
    // carry over an outcome the new form does not offer.
    return (
      <AuthedFeedbackForm
        key={`${state.feedbackSessionId}:${state.feedbackSessionStatus ?? ""}`}
        plannedSessionId={state.feedbackSessionId}
        sessionStatus={state.feedbackSessionStatus}
      />
    );
  }
  // No session id => the guest demo. Signed in, there is nothing truthful to
  // show here, so show nothing.
  if (token != null) return null;
  return <GuestFeedbackPreview />;
}

// ──────────────────────────────────────────────────────────────────────────
// Authenticated: a real report against a real session.
// ──────────────────────────────────────────────────────────────────────────

interface FeedbackForm {
  outcome: Outcome;
  modifiedVolume: boolean;
  modifiedIntensity: boolean;
  modifiedExercises: boolean;
  reason: string;
  satisfaction: number | null;
  pain: boolean;
  soreness: boolean;
  notes: string;
}

const EMPTY_FORM: FeedbackForm = {
  outcome: "completed",
  modifiedVolume: false,
  modifiedIntensity: false,
  modifiedExercises: false,
  reason: "",
  satisfaction: null,
  pain: false,
  soreness: false,
  notes: "",
};

/**
 * Build the request body. Pure and exported for test, so the mapping from what
 * the athlete clicked to what gets stored can be asserted without a render.
 *
 * `followed_as_prescribed` is only claimed when the athlete actually said so:
 * "completed" means followed, "modified" means not, and a skip leaves it null
 * rather than asserting a session that never happened was or wasn't followed.
 */
export function buildFeedbackBody(
  plannedSessionId: number,
  f: FeedbackForm,
): SessionFeedbackIn {
  const modified = f.outcome === "modified";
  const trimmedReason = f.reason.trim();
  const trimmedNotes = f.notes.trim();
  return {
    planned_session_id: plannedSessionId,
    status: f.outcome,
    followed_as_prescribed:
      f.outcome === "completed" ? true : modified ? false : null,
    // Only a modification carries modification flags — a completed-as-prescribed
    // session must not report that something changed.
    modified_volume: modified && f.modifiedVolume,
    modified_intensity: modified && f.modifiedIntensity,
    modified_exercises: modified && f.modifiedExercises,
    modification_reason: modified && trimmedReason ? trimmedReason : null,
    skip_reason: f.outcome === "skipped" && trimmedReason ? trimmedReason : null,
    satisfaction_score: f.satisfaction,
    pain_flag: f.pain,
    soreness_flag: f.soreness,
    notes: trimmedNotes || null,
  };
}

const inputCls =
  "mt-2 w-full rounded-[11px] border border-white/10 bg-panel px-[13px] py-[11px] text-[14px] text-ink";
const segCls = (active: boolean) =>
  cn(
    "flex-1 cursor-pointer rounded-[10px] border p-[11px] text-center text-[13px] font-semibold leading-none",
    active ? "border-ac/40 bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute",
  );
const chipCls = (active: boolean) =>
  cn(
    "cursor-pointer rounded-full border px-[13px] py-[8px] text-[12px] font-semibold leading-none",
    active ? "border-ac/40 bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute",
  );

function AuthedFeedbackForm({
  plannedSessionId,
  sessionStatus,
}: {
  plannedSessionId: number;
  sessionStatus: SessionStatus | null;
}) {
  const { actions } = usePerfLab();
  const auth = useAuth();
  const outcomes = outcomesFor(sessionStatus);
  // The draft starts on an offered outcome, and the form is remounted (keyed above) whenever
  // the session or its status changes, so what is shown is what is sent.
  const [form, setForm] = useState<FeedbackForm>({ ...EMPTY_FORM, outcome: outcomes[0][0] });
  const missed = sessionStatus === "missed";
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState<SessionFeedbackOut | null>(null);

  const set = <K extends keyof FeedbackForm>(key: K, value: FeedbackForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  async function save() {
    if (!auth.token || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const row = await createSessionFeedback(
        buildFeedbackBody(plannedSessionId, form),
        auth.token,
      );
      setSaved(row);
      // The session's own state may have moved (and the adherence signal now
      // includes this report), so tell dependents to re-read rather than
      // patching anything locally.
      actions.refreshFeedback();
    } catch (e) {
      const err = e as ApiError;
      // 409 is the one status worth translating: it means either the session has
      // not finished yet or this report already exists. Both are states the
      // athlete can act on, unlike a bare "request failed".
      setSaveError(
        err?.status === 409
          ? (err.message ?? "That session already has feedback, or hasn't finished yet.")
          : (err?.message ??
            "Couldn't record that — check you're signed in and the backend is reachable.")
      );
    } finally {
      setSaving(false);
    }
  }

  const modified = form.outcome === "modified";

  return (
    <Shell>
      {saved ? (
        <div className="flex flex-col items-center gap-4 px-9 py-10 text-center">
          <div className="grid h-[62px] w-[62px] place-items-center rounded-full border border-good/35 bg-good/[0.12]">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#5fd08a" strokeWidth="2.2">
              <path d="M20 6 9 17l-5-5" />
            </svg>
          </div>
          <div className="text-[22px] font-bold leading-[1.2] text-ink">Feedback recorded</div>
          {/* Says only what the write actually did. Feedback is a label, not a
              dose: it does not advance S(t), and claiming otherwise was the
              specific lie this screen used to tell. */}
          <div className="max-w-[400px] text-[13.5px] font-medium leading-[1.6] text-mute">
            {missed ? (
              // P2: a miss stays a miss; this report explains it and changes nothing else.
              <>
                Saved against this session as why it was missed. The session stays missed,
                and this report doesn't change your prescription or your state.
              </>
            ) : (
              <>
                Saved against this session. Repeated skips or changes bias your next
                prescription toward lighter work — your state vector is unchanged until
                you log a workout.
              </>
            )}
          </div>
          <div className="mt-[10px] flex gap-[10px]">
            <button
              onClick={actions.closeFeedback}
              className="rounded-[10px] border border-white/10 bg-white/[0.04] px-[18px] py-3 text-[12.5px] font-semibold leading-none text-soft"
            >
              Done
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-5">
            <div>
              <h2 className="m-0 text-[18px] font-bold leading-none text-ink">How did that session go?</h2>
              <div className="mt-[6px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">
                Session #{plannedSessionId}
              </div>
            </div>
            <CloseBtn onClick={actions.closeFeedback} />
          </div>

          <div className="flex flex-col gap-[22px] p-6">
            {missed && (
              <p className="m-0 text-[12.5px] leading-[1.45] text-soft">
                Nothing was logged for this session. Tell us why it didn't happen.
              </p>
            )}
            <Field label="Outcome">
              <div className="flex gap-2">
                {outcomes.map(([key, label]) => (
                  <div key={key} onClick={() => set("outcome", key)} className={segCls(form.outcome === key)}>
                    {label}
                  </div>
                ))}
              </div>
            </Field>

            {modified && (
              <Field label="What changed?">
                <div className="flex flex-wrap gap-2">
                  <div onClick={() => set("modifiedVolume", !form.modifiedVolume)} className={chipCls(form.modifiedVolume)}>
                    Volume
                  </div>
                  <div onClick={() => set("modifiedIntensity", !form.modifiedIntensity)} className={chipCls(form.modifiedIntensity)}>
                    Intensity
                  </div>
                  <div onClick={() => set("modifiedExercises", !form.modifiedExercises)} className={chipCls(form.modifiedExercises)}>
                    Exercises
                  </div>
                </div>
              </Field>
            )}

            {form.outcome !== "completed" && (
              <Field label={modified ? "Why did you change it?" : "Why did you skip it?"}>
                <input
                  value={form.reason}
                  onChange={(e) => set("reason", e.target.value)}
                  placeholder="Optional"
                  className={inputCls}
                />
              </Field>
            )}

            <Field label="How well did it fit?">
              <div className="flex gap-2">
                {[1, 2, 3, 4, 5].map((n) => (
                  <div
                    key={n}
                    onClick={() => set("satisfaction", form.satisfaction === n ? null : n)}
                    className={segCls(form.satisfaction === n)}
                  >
                    {n}
                  </div>
                ))}
              </div>
              <div className="mt-2 text-[11px] font-medium leading-none text-dim">
                {form.satisfaction == null ? "Not reported" : "1 = poor fit · 5 = ideal"}
              </div>
            </Field>

            <Field label="Anything flag up?">
              <div className="flex flex-wrap gap-2">
                <div onClick={() => set("pain", !form.pain)} className={chipCls(form.pain)}>
                  Pain
                </div>
                <div onClick={() => set("soreness", !form.soreness)} className={chipCls(form.soreness)}>
                  Unusual soreness
                </div>
              </div>
            </Field>

            <Field label="Notes">
              <input
                value={form.notes}
                onChange={(e) => set("notes", e.target.value)}
                placeholder="Optional"
                className={inputCls}
              />
            </Field>
          </div>

          <div className="flex items-center justify-between gap-[9px] border-t border-white/[0.06] px-6 py-4">
            <span
              className={cn(
                "max-w-[330px] text-[11px] font-medium leading-[1.4]",
                saveError ? "text-hot" : "text-dim",
              )}
            >
              {saveError ??
                (missed
                  ? "Kept with this missed session. It doesn't change your prescription."
                  : "Recorded against this session and used to bias your next prescription.")}
            </span>
            <div className="flex flex-none gap-[9px]">
              <button
                onClick={actions.closeFeedback}
                className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft"
              >
                Cancel
              </button>
              <button
                onClick={() => void save()}
                disabled={saving}
                className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-[18px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60"
              >
                {saving ? "Saving…" : "Record feedback →"}
              </button>
            </div>
          </div>
        </>
      )}
    </Shell>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-3 font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">
        {label}
      </div>
      {children}
    </div>
  );
}

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), textarea, select, a[href], [tabindex]:not([tabindex="-1"])';

/**
 * A modal dialog: focus moves into it on open and Tab cycles inside it, so nothing behind
 * the backdrop (another session's Feedback button, say) can be reached from the keyboard.
 */
function Shell({ children }: { children: ReactNode }) {
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    dialog.current?.focus();
  }, []);

  function containTab(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "Tab" || !dialog.current) return;
    const items = Array.from(dialog.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) {
      e.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    const inside = active instanceof Node && dialog.current.contains(active);
    if (e.shiftKey && (active === first || active === dialog.current || !inside)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !inside)) {
      e.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      className="fixed inset-0 z-[63] flex items-center justify-center p-8 backdrop-blur-[5px]"
      style={{ background: "rgba(4,5,8,.72)" }}
    >
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        onKeyDown={containTab}
        className="max-h-[92vh] w-[720px] max-w-full overflow-auto rounded-[18px] border border-white/[0.09] bg-surface shadow-[0_50px_110px_-30px_rgba(0,0,0,.78)] outline-none"
      >
        {children}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Guest preview — fixture data, unauthenticated only. See the header note.
// ──────────────────────────────────────────────────────────────────────────

const STATS: [string, ReactNode, string][] = [
  ["Distance", "9.1 km", "text-ink"],
  ["Duration", "53:20", "text-ink"],
  ["Avg pace", <>4:32<span className="text-[10px] text-faint"> /km</span></>, "text-ink"],
  ["Avg HR", <>168<span className="text-[10px] text-faint"> bpm</span></>, "text-hot"],
];

const FEELS: [Feel, string, number][] = [
  ["easy", "Easy", 4],
  ["controlled", "Controlled", 6],
  ["hard", "Hard", 8],
  ["maxed", "Maxed", 10],
];

function GuestFeedbackPreview() {
  const { state, actions } = usePerfLab();
  const { readyAfter, fatAfter, capDelta, cap, readyColor } = projectLogDose(state);

  return (
    <Shell>
      {!state.feedbackApplied ? (
        <div>
          <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-5">
            <div className="flex items-center gap-[11px]">
              <span className="h-2 w-2 rounded-full bg-good" />
              <div>
                <h2 className="m-0 text-[18px] font-bold leading-none text-ink">Session complete</h2>
                <div className="mt-[6px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">Sample data · sign in to record real feedback</div>
              </div>
            </div>
            <CloseBtn onClick={actions.closeFeedback} />
          </div>
          <div className="flex flex-col gap-[22px] p-6">
            <div className="grid grid-cols-2 gap-[14px] sm:grid-cols-4">
              {STATS.map(([label, value, color]) => (
                <div key={label} className="rounded-[13px] border border-white/[0.06] bg-tile p-[14px]">
                  <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-faint">{label}</div>
                  <div className={`mt-[9px] font-mono text-[19px] font-semibold leading-none ${color}`}>{value}</div>
                </div>
              ))}
            </div>
            <div>
              <div className="mb-3 font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">How did it feel?</div>
              <div className="flex gap-2">
                {FEELS.map(([key, label, rpe]) => (
                  <div
                    key={key}
                    onClick={() => actions.setFeel(key, rpe)}
                    className={cn("flex-1 cursor-pointer rounded-[9px] border px-[6px] py-[10px] text-center text-[12px] font-semibold leading-none", state.feel === key ? "border-ac/40 bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute")}
                  >
                    {label}
                  </div>
                ))}
              </div>
            </div>
            <div>
              <div className="mb-[10px] flex items-center justify-between">
                <span className="font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">Perceived effort</span>
                <span className={cn("font-mono text-[14px] font-semibold leading-none", state.rpe === null ? "text-dim" : "text-ac")}>{state.rpe ?? "—"} <span className="text-[11px] text-dim">/ 10 RPE</span></span>
              </div>
              <input type="range" min={1} max={10} value={state.rpe ?? 7} onChange={(e) => actions.setRpe(+e.target.value)} className="w-full cursor-pointer" style={{ accentColor: "var(--ac)" }} />
            </div>
            <div className="rounded-[14px] border border-white/[0.07] bg-white/[0.02] px-[18px] py-4">
              <div className="mb-[14px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">How your twin would update</div>
              <div className="grid grid-cols-3 gap-4">
                <TwinDelta label="Readiness" from="64" to={`${readyAfter}`} toColor={readyColor} />
                <TwinDelta label="Mean fatigue" from="33" to={`${fatAfter}`} toColor={COLORS.hot} />
                <TwinDelta label={cap} drive to={capDelta} toColor={COLORS.teal} />
              </div>
            </div>
          </div>
          <div className="flex items-center justify-end gap-[9px] border-t border-white/[0.06] px-6 py-4">
            <button onClick={actions.closeFeedback} className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-[11px] text-[12.5px] font-semibold leading-none text-soft">Discard</button>
            <button onClick={actions.applyFeedback} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-[18px] py-[11px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">See the projection →</button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-4 px-9 py-10 text-center">
          <div className="grid h-[62px] w-[62px] place-items-center rounded-full border border-good/35 bg-good/[0.12]">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#5fd08a" strokeWidth="2.2"><path d="M20 6 9 17l-5-5" /></svg>
          </div>
          <div className="text-[22px] font-bold leading-[1.2] text-ink">Sample projection</div>
          <div className="max-w-[380px] text-[13.5px] font-medium leading-[1.6] text-mute">This is what folding a session into S(t) looks like. Nothing was saved — sign in to train a real twin.</div>
          <div className="mt-[6px] grid w-full max-w-[440px] grid-cols-3 gap-[14px]">
            <ResultCard label="Readiness" value={`64 → ${readyAfter}`} color={readyColor} />
            <ResultCard label="Fatigue" value={`33 → ${fatAfter}`} color={COLORS.hot} />
            <ResultCard label={cap} value={capDelta} color={COLORS.teal} />
          </div>
          <div className="mt-[10px] flex gap-[10px]">
            <button onClick={actions.closeFeedback} className="rounded-[10px] border border-white/10 bg-white/[0.04] px-[18px] py-3 text-[12.5px] font-semibold leading-none text-soft">Done</button>
            <button onClick={actions.feedbackToTwin} className="rounded-[10px] bg-gradient-to-r from-mint to-teal px-5 py-3 text-[12.5px] font-semibold leading-none text-[#0a0c10]">View twin →</button>
          </div>
        </div>
      )}
    </Shell>
  );
}

function TwinDelta({ label, from, to, toColor, drive }: { label: string; from?: string; to: string; toColor: string; drive?: boolean }) {
  return (
    <div>
      <div className="text-[11px] font-medium leading-none text-mute">{label}</div>
      <div className="mt-2 flex items-baseline gap-[6px]">
        <span className="font-mono text-[17px] font-semibold leading-none text-soft">{drive ? "drive" : from}</span>
        {!drive && <span className="text-[12px] font-medium leading-none text-dim">→</span>}
        <span className="font-mono text-[17px] font-semibold leading-none" style={{ color: toColor }}>{to}</span>
      </div>
    </div>
  );
}

function ResultCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="rounded-[13px] border border-white/[0.06] bg-tile p-[15px]">
      <div className="text-[11px] font-medium leading-none text-mute">{label}</div>
      <div className="mt-2 font-mono text-[18px] font-semibold leading-none" style={{ color }}>{value}</div>
    </div>
  );
}
