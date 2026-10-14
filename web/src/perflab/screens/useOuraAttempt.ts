// src/perflab/screens/useOuraAttempt.ts
//
// Settings' half of an Oura OAuth attempt (P6b, ADR-0075): start it with a PKCE challenge, and
// finish it when Oura sends the browser back to /settings/oura-callback.
//
// The server decides every outcome; this tab only keeps the attempt's inputs until it recognizes
// the outcome as terminal or the athlete starts over. An unreadable reply, an unknown status or a
// lost reply is uncertain and keeps them (src/api/ouraReply.ts).
//
// Every operation (Connect, finishing a return, Start over) takes a ticket: its number and the
// identity that began it. A reply acts only while its ticket is still the latest and that identity
// is still signed in, and a reply about an attempt also needs that attempt still stored. So a late
// reply for an abandoned attempt, an earlier operation, or another account changes nothing.
// "Connected" is only ever what GET /connection says: a success receipt is history.
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "@/api/perfLabClient";
import { outcomeOf } from "@/api/ouraReply";
import type { ApiError, ConnectionStatus, OAuthAttemptResult } from "@/types";
import {
  authorizeUrlCarries,
  challengeFor,
  clearAttemptIf,
  generateVerifier,
  isCurrentAttempt,
  loadAttempt,
  once,
  ouraNav,
  saveAttempt,
  type OuraAttemptRecord,
} from "../ouraPkce";
import { OURA_UPDATE_INTERRUPTED_MESSAGE, peekOuraReturn, takeOuraReturn } from "../wearableSync";

export const OURA_MESSAGES = {
  reload: "Perf Lab was updated. Reload the page, then Connect Oura again.",
  mismatch: "This sign-in link doesn't belong to the attempt started here.",
  connected: "Oura connected.",
  linkedButNotShown: "Oura sign-in finished, but Perf Lab shows no Oura connection. Try Connect Oura again.",
  linkedStatusUnknown:
    "Oura sign-in finished, but the connection status couldn't be loaded. Reload Settings to check.",
  expired: "That sign-in link expired. Connect Oura again.",
  cancelled: "Oura sign-in was cancelled.",
  didNotComplete: "Oura sign-in didn't complete. Connect Oura again.",
  stillFinishing: "Still finishing the Oura sign-in…",
  wrongAccount: "Sign in as the account that started this Oura sign-in.",
  signIn: "Sign in to finish connecting Oura.",
  storageBlocked:
    "Perf Lab couldn't save this sign-in in your browser. Allow site storage for Perf Lab (or leave private browsing), then Connect Oura again.",
  legacyConnected: "Oura connected. Your data syncs each time you open Perf Lab.",
  legacyError: "Oura sign-in didn't complete. Try Connect Oura again.",
} as const;

export interface OuraAttemptView {
  tone: "notice" | "error";
  text: string;
  /** Offer Retry: resend the stored callback inputs. */
  retry?: boolean;
  /** Offer Start over: forget this attempt. */
  startOver?: boolean;
}

type Op = "complete" | "cancel";

/** One operation's claim on this screen. */
interface Ticket {
  n: number;
  userKey: string;
}

function failedText(reason: string | null | undefined): string {
  if (reason === "expired") return OURA_MESSAGES.expired;
  if (reason === "cancelled") return OURA_MESSAGES.cancelled;
  return OURA_MESSAGES.didNotComplete;
}

/** The message an undelivered Oura return implies before anything is sent. Pure (a cached read
 *  of the return and of storage), so StrictMode's double call of the initializer is harmless. */
function initialView(): OuraAttemptView | null {
  const r = peekOuraReturn();
  if (r === null) return null;
  if (r.kind === "connected") return { tone: "notice", text: OURA_MESSAGES.legacyConnected };
  if (r.kind === "error")
    return {
      tone: "error",
      text: r.reason === "interrupted" ? OURA_UPDATE_INTERRUPTED_MESSAGE : OURA_MESSAGES.legacyError,
    };
  const rec = loadAttempt();
  if (!rec || !r.state || rec.state !== r.state) return { tone: "error", text: OURA_MESSAGES.mismatch };
  return null;
}

export function useOuraAttempt(opts: {
  token: string | null | undefined;
  userKey: string;
  /** GET /connection: the only source of "connected". Reads only; nothing is shown from it until
   *  the operation that asked is checked to still be current. */
  readConnection: (token: string) => Promise<ConnectionStatus>;
  /** Show a connection status read for the current operation (the card). False when the card
   *  refused it because a newer read already owns the card: then nothing is said from it. */
  showConnection: (status: ConnectionStatus) => boolean;
}) {
  const { token, userKey, readConnection, showConnection } = opts;
  const [view, setView] = useState<OuraAttemptView | null>(initialView);
  const identity = useRef({ token, userKey, readConnection, showConnection });
  useEffect(() => {
    identity.current = { token, userKey, readConnection, showConnection };
  });

  const epoch = useRef(0);
  // Leaving Settings ends every operation begun here: a late reply must not navigate, store or
  // speak for a screen that's gone. Advancing the epoch kills every ticket issued so far, including
  // across StrictMode's simulated remount.
  useEffect(
    () => () => {
      // A counter, not a DOM node: advancing its value at cleanup time is the point.
      epoch.current++;
    },
    [],
  );
  /** A new operation: every earlier one's replies stop acting. */
  const begin = (): Ticket => ({ n: ++epoch.current, userKey: identity.current.userKey });
  /** No later operation began (unmounting counts as one), and the identity that began this one is
   *  still signed in. */
  const live = (t: Ticket) => epoch.current === t.n && identity.current.userKey === t.userKey;
  /** ...and the attempt it is about is still the stored one, for that identity. */
  const mine = (rec: OuraAttemptRecord, t: Ticket) =>
    live(t) && rec.userKey === t.userKey && isCurrentAttempt(rec.state, rec.userKey);

  const keep = (rec: OuraAttemptRecord, t: Ticket, v: OuraAttemptView) => {
    if (mine(rec, t)) setView(v);
  };

  /** Terminal: clear this attempt (only if still current), then say so. */
  const finish = (rec: OuraAttemptRecord, t: Ticket, v: OuraAttemptView) => {
    if (!mine(rec, t)) return;
    clearAttemptIf(rec.state, rec.userKey);
    setView(v);
  };

  const linked = async (rec: OuraAttemptRecord, t: Ticket) => {
    if (!mine(rec, t)) return;
    clearAttemptIf(rec.state, rec.userKey);
    const tok = identity.current.token;
    let v: OuraAttemptView;
    try {
      const s = tok ? await identity.current.readConnection(tok) : null;
      if (!live(t)) return; // a newer operation or another account owns the screen now
      // A newer read already owns the card; a message from this older one could contradict it.
      if (s && !identity.current.showConnection(s)) return;
      v = s?.connected
        ? { tone: "notice", text: OURA_MESSAGES.connected }
        : { tone: "error", text: OURA_MESSAGES.linkedButNotShown };
    } catch {
      v = { tone: "error", text: OURA_MESSAGES.linkedStatusUnknown };
    }
    if (live(t)) setView(v);
  };

  const settle = async (rec: OuraAttemptRecord, op: Op) => {
    const t = begin();
    const tok = identity.current.token;
    if (!tok) {
      // Signed out: no identity to match the attempt against, so `keep` (which requires one)
      // would show nothing. The attempt is still this tab's; say how to finish it.
      if (isCurrentAttempt(rec.state, rec.userKey))
        setView({ tone: "error", text: OURA_MESSAGES.signIn });
      return;
    }
    if (t.userKey !== rec.userKey) {
      if (isCurrentAttempt(rec.state, rec.userKey))
        setView({ tone: "error", text: OURA_MESSAGES.wrongAccount, startOver: true });
      return;
    }
    let result: OAuthAttemptResult | null = null;
    let err: unknown = null;
    try {
      result = await once(op, rec.userKey, rec.state, () =>
        op === "complete"
          ? api.completeOuraAuthorization(
              { state: rec.state, code: rec.callback?.code ?? "", code_verifier: rec.verifier },
              tok,
            )
          : api.cancelOuraAuthorization({ state: rec.state, code_verifier: rec.verifier }, tok),
      );
    } catch (e) {
      err = e;
    }

    const outcome = outcomeOf(result, err);
    switch (outcome.kind) {
      case "succeeded":
        // A cancel can find the attempt already succeeded: that is a success, never "cancelled".
        await linked(rec, t);
        return;
      case "failed":
        finish(rec, t, { tone: "error", text: failedText(outcome.reason) });
        return;
      case "refused":
        finish(rec, t, { tone: "error", text: OURA_MESSAGES.didNotComplete });
        return;
      case "signIn":
        keep(rec, t, { tone: "error", text: OURA_MESSAGES.signIn });
        return;
      case "wrongAccount":
        keep(rec, t, { tone: "error", text: OURA_MESSAGES.wrongAccount, startOver: true });
        return;
      default:
        // In progress, 503 outcome_unknown, a lost or unreadable reply, an unknown status: the
        // outcome is not known here. Keep the inputs; Retry asks the server again.
        keep(rec, t, { tone: "notice", text: OURA_MESSAGES.stillFinishing, retry: true, startOver: true });
    }
  };

  /** The return's inputs, also kept in memory: if storage refuses them (blocked or full), this page
   *  can still finish or retry the sign-in. Used only while the stored attempt is still that one. */
  const returned = useRef<OuraAttemptRecord | null>(null);
  const withCallback = (): OuraAttemptRecord | null => {
    const rec = loadAttempt();
    if (!rec || rec.callback) return rec;
    const mem = returned.current;
    return mem && mem.state === rec.state && mem.userKey === rec.userKey ? mem : rec;
  };

  const resume = () => {
    const rec = withCallback();
    if (!rec?.callback) return;
    if (rec.callback.error) void settle(rec, "cancel");
    else if (rec.callback.code) void settle(rec, "complete");
    else finish(rec, begin(), { tone: "error", text: OURA_MESSAGES.didNotComplete });
  };
  const resumeRef = useRef(resume);
  useEffect(() => {
    resumeRef.current = resume;
  });

  // The return from Oura, delivered once per page load (StrictMode replays this effect). What
  // it says on screen is derived up front (initialView); the effect only does the side effects.
  useEffect(() => {
    const r = takeOuraReturn();
    if (r === null) return;
    if (r.kind !== "callback") {
      window.history.replaceState(null, "", window.location.pathname);
      return;
    }
    window.history.replaceState(null, "", "/settings");
    const rec = loadAttempt();
    // Not this tab's attempt: initialView says so, and whatever attempt is stored is left alone.
    if (!rec || !r.state || rec.state !== r.state) return;
    const back = { ...rec, callback: { code: r.code ?? undefined, error: r.error ?? undefined } };
    returned.current = back;
    saveAttempt(back); // if storage refuses, `returned` still lets this page finish it
    resumeRef.current();
  }, []);

  // Resume a stored attempt once its identity is signed in (after a 401, a reload, a re-login).
  useEffect(() => {
    if (!token) return;
    const rec = withCallback();
    if (rec?.callback && rec.userKey === userKey) resumeRef.current();
  }, [token, userKey]);

  const connect = useCallback(async () => {
    if (!identity.current.token) return;
    const t = begin();
    const verifier = generateVerifier();
    const challenge = await challengeFor(verifier);
    // The digest is async: another account, a newer operation or leaving Settings may have come
    // first. Read the token only now, for the identity this ticket still names.
    if (!live(t)) return;
    const tok = identity.current.token;
    if (!tok) return;
    let url: string;
    try {
      url = (await api.getOuraAuthorizeUrl(tok, challenge)).authorize_url;
    } catch (e) {
      if (live(t)) setView({ tone: "error", text: (e as ApiError).message ?? "Could not start Oura sign-in" });
      return;
    }
    // Another account signed in, or a newer operation began, while this was out: not ours.
    if (!live(t)) return;
    const state = authorizeUrlCarries(url, challenge);
    if (state === null) {
      // The server did not bind the attempt to this tab's challenge: never open it.
      setView({ tone: "error", text: OURA_MESSAGES.reload });
      return;
    }
    if (!saveAttempt({ state, verifier, challenge, startedAt: Date.now(), userKey: t.userKey })) {
      // Without the stored verifier the return could never be finished: don't send the athlete.
      setView({ tone: "error", text: OURA_MESSAGES.storageBlocked });
      return;
    }
    setView(null);
    ouraNav.assign(url);
  }, []);

  const retry = useCallback(() => resumeRef.current(), []);

  const startOver = useCallback(() => {
    begin();
    const rec = loadAttempt();
    if (rec) clearAttemptIf(rec.state, rec.userKey);
    setView(null);
  }, []);

  return { view, connect, retry, startOver };
}
