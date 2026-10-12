// src/perflab/screens/useSubmissionKey.ts
//
// One idempotency key per LOGICAL strength submission (P4-3), not per button press.
//
// Pressing Save is a new submission: it freezes the body it sends, with a new key. That frozen
// pair is an ATTEMPT. A confirmed outcome (a response that parses as a StrengthOutcome) resolves
// it. A 4xx on the attempt's FIRST send resolves it too: the server answered and wrote nothing.
// Anything else — no answer, a 5xx, a reply that isn't a valid outcome — leaves it UNCERTAIN: it
// may or may not have been recorded. An uncertain attempt is kept until a confirmed outcome or an
// explicit Discard. Retry resends exactly that body with exactly that key (the server answers
// with the first outcome instead of recording the set twice); a refusal of a retry (401, 429,
// 422…) does not prove the original send failed, so the attempt stays uncertain. Discard only
// forgets the attempt: it does not undo a save that may already be recorded.
//
// Attempts are kept per SLOT — one per lift in Settings, one per card in Assess — so one slot's
// sends never touch another's attempt. A slot holds one attempt at a time: no second send starts
// while one is in flight, Save is refused while an uncertain attempt is waiting, and a settling
// response only changes the slot if it belongs to the attempt still there.
//
// Attempts live in the screen's memory only. Leaving the screen (navigation unmounts it) or
// reloading forgets them.
//
// The body builder (strengthEvidenceBody.ts) stays pure; the key is attached here, at send time.
import { useCallback, useReducer, useRef } from "react";
import type { ApiError, StrengthEvidenceCreate } from "@/types";

/** One frozen submission: the body exactly as sent, including its key. */
export type SubmissionAttempt = Readonly<StrengthEvidenceCreate> & { readonly idempotency_key: string };

/** Whether a failed send may still have been recorded. `perfLabClient`'s `handleResponse` throws
 *  `{ message, status: res.status }` for every response it received (perfLabClient.ts:127-131);
 *  a received 4xx is a refusal that wrote nothing. Anything else — no status (fetch threw, or the
 *  reply was not a valid outcome), status 0, or a 5xx — leaves the outcome unknown. */
export function isUncertainFailure(error: unknown): boolean {
  const status = (error as ApiError | null | undefined)?.status;
  return !(typeof status === "number" && status >= 400 && status < 500);
}

type Entry = {
  readonly attempt: SubmissionAttempt;
  /** A send of this attempt is in flight. */
  inFlight: boolean;
  /** Some send of this attempt ended without a confirmed outcome. */
  uncertain: boolean;
};

/** The attempt logic without React. */
export function createSubmissionAttempts(mint: () => string = () => crypto.randomUUID()) {
  const slots = new Map<string, Entry>();

  async function send<T>(slot: string, entry: Entry, submit: (body: SubmissionAttempt) => Promise<T>): Promise<T> {
    entry.inFlight = true;
    try {
      const result = await submit(entry.attempt);
      if (slots.get(slot) === entry) slots.delete(slot);
      return result;
    } catch (error) {
      entry.inFlight = false;
      // Only this attempt's own settlement changes the slot (it may have been discarded).
      if (slots.get(slot) === entry) {
        if (!entry.uncertain && !isUncertainFailure(error)) slots.delete(slot);
        else entry.uncertain = true;
      }
      throw error;
    }
  }

  return {
    /** A new submission for the slot: the body frozen with a new key, then sent. Refused while
     *  the slot has a send in flight or an uncertain attempt waiting for Retry or Discard. */
    save<T>(slot: string, body: StrengthEvidenceCreate, submit: (body: SubmissionAttempt) => Promise<T>): Promise<T> {
      if (slots.has(slot)) return Promise.reject(new Error(`${slot} already has a save in flight or unresolved`));
      const facts: StrengthEvidenceCreate = { ...body };
      delete facts.idempotency_key;
      const entry: Entry = {
        attempt: Object.freeze({ ...facts, idempotency_key: mint() }),
        inFlight: false,
        uncertain: false,
      };
      slots.set(slot, entry);
      return send(slot, entry, submit);
    },
    /** Resend the slot's uncertain attempt unchanged. */
    retry<T>(slot: string, submit: (body: SubmissionAttempt) => Promise<T>): Promise<T> {
      const entry = slots.get(slot);
      if (entry === undefined || !entry.uncertain) return Promise.reject(new Error(`nothing to retry for ${slot}`));
      if (entry.inFlight) return Promise.reject(new Error(`${slot} already has a retry in flight`));
      return send(slot, entry, submit);
    },
    /** Forget the slot's uncertain attempt; the next Save is a new submission. It does not undo a
     *  save that may already be recorded. Ignored while a send is in flight. */
    discard(slot: string): void {
      const entry = slots.get(slot);
      if (entry !== undefined && !entry.inFlight) slots.delete(slot);
    },
    /** The slot's uncertain attempt, while it waits for Retry or Discard (and while it is being
     *  retried). */
    unresolved(slot: string): SubmissionAttempt | undefined {
      const entry = slots.get(slot);
      return entry?.uncertain ? entry.attempt : undefined;
    },
    /** Whether a send for the slot is in flight. */
    inFlight(slot: string): boolean {
      return slots.get(slot)?.inFlight === true;
    },
  };
}

/** The attempts for one screen, re-rendering when a slot's state changes. */
export function useSubmissionAttempts() {
  const ref = useRef<ReturnType<typeof createSubmissionAttempts> | null>(null);
  if (ref.current === null) ref.current = createSubmissionAttempts();
  const attempts = ref.current;
  const [, rerender] = useReducer((n: number) => n + 1, 0);

  // A send changes its slot when it starts (in flight) and when it settles.
  const track = useCallback(async <T,>(run: Promise<T>): Promise<T> => {
    rerender();
    try {
      return await run;
    } finally {
      rerender();
    }
  }, []);
  const save = useCallback(
    <T,>(slot: string, body: StrengthEvidenceCreate, submit: (body: SubmissionAttempt) => Promise<T>) =>
      track(attempts.save(slot, body, submit)),
    [attempts, track],
  );
  const retry = useCallback(
    <T,>(slot: string, submit: (body: SubmissionAttempt) => Promise<T>) => track(attempts.retry(slot, submit)),
    [attempts, track],
  );
  const discard = useCallback(
    (slot: string) => {
      attempts.discard(slot);
      rerender();
    },
    [attempts],
  );
  const unresolved = useCallback((slot: string) => attempts.unresolved(slot), [attempts]);
  const inFlight = useCallback((slot: string) => attempts.inFlight(slot), [attempts]);
  return { save, retry, discard, unresolved, inFlight };
}
