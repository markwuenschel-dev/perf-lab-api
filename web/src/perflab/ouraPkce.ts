// src/perflab/ouraPkce.ts
//
// The browser half of an Oura OAuth attempt (P6b, ADR-0075). The browser makes a PKCE verifier,
// sends only its S256 challenge to start the attempt, keeps the verifier in sessionStorage, and
// proves it when Oura sends the browser back. Everything here is scoped to ONE attempt: its
// `state` and the identity (`userKey`) that started it. A reply for any other attempt, or for a
// different signed-in identity, never clears or overwrites this one.
import { OURA_PKCE_STORAGE_KEY } from "./wearableSync";

/** What this tab remembers about the attempt it started. */
export interface OuraAttemptRecord {
  state: string;
  verifier: string;
  challenge: string;
  startedAt: number;
  /** The signed-in identity that started it. */
  userKey: string;
  /** What Oura sent back, captured once from the callback URL and kept until the outcome is
   *  terminal or the athlete starts over, so Retry and resume-after-sign-in can resend it. */
  callback?: { code?: string; error?: string };
}

function base64url(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** A 64-character verifier (RFC 7636 allows 43-128 unreserved characters). */
export function generateVerifier(): string {
  const bytes = new Uint8Array(48);
  crypto.getRandomValues(bytes);
  return base64url(bytes);
}

export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

/** The attempt's `state` if `url` carries exactly our challenge with method S256, else null.
 *  A consent URL that doesn't is never opened: the attempt would not be bound to this tab. */
export function authorizeUrlCarries(url: string, challenge: string): string | null {
  let params: URLSearchParams;
  try {
    params = new URL(url).searchParams;
  } catch {
    return null;
  }
  const challenges = params.getAll("code_challenge");
  const methods = params.getAll("code_challenge_method");
  const states = params.getAll("state");
  if (challenges.length !== 1 || challenges[0] !== challenge) return null;
  if (methods.length !== 1 || methods[0] !== "S256") return null;
  if (states.length !== 1 || !states[0]) return null;
  return states[0];
}

function isRecord(v: unknown): v is OuraAttemptRecord {
  if (typeof v !== "object" || v === null) return false;
  const r = v as Record<string, unknown>;
  return (
    typeof r.state === "string" &&
    typeof r.verifier === "string" &&
    typeof r.challenge === "string" &&
    typeof r.startedAt === "number" &&
    typeof r.userKey === "string"
  );
}

export function loadAttempt(): OuraAttemptRecord | null {
  try {
    const raw = window.sessionStorage.getItem(OURA_PKCE_STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** Store the attempt. False when the browser refused (blocked or full storage, some private
 *  modes): the verifier is then not kept, so the attempt could never be finished from this tab
 *  and must not be started. */
export function saveAttempt(record: OuraAttemptRecord): boolean {
  try {
    window.sessionStorage.setItem(OURA_PKCE_STORAGE_KEY, JSON.stringify(record));
  } catch {
    return false;
  }
  const kept = loadAttempt();
  return kept !== null && kept.state === record.state && kept.verifier === record.verifier;
}

/** Is the stored attempt still this one, for this identity? Checked before every clear and
 *  every screen update that follows an await. */
export function isCurrentAttempt(state: string, userKey: string): boolean {
  const r = loadAttempt();
  return r !== null && r.state === state && r.userKey === userKey;
}

/** Forget the attempt, but only if it is still the one named: a newer attempt survives. */
export function clearAttemptIf(state: string, userKey: string): void {
  if (!isCurrentAttempt(state, userKey)) return;
  try {
    window.sessionStorage.removeItem(OURA_PKCE_STORAGE_KEY);
  } catch {
    // storage unavailable: nothing to clear
  }
}

const inFlight = new Map<string, Promise<unknown>>();

/** Share one outstanding request per (operation, identity, attempt). StrictMode replays and
 *  concurrent resumes join it; once it settles the entry is gone, so Retry sends a new one. */
export function once<T>(op: string, userKey: string, state: string, run: () => Promise<T>): Promise<T> {
  const key = `${op}|${userKey}|${state}`;
  const existing = inFlight.get(key);
  if (existing) return existing as Promise<T>;
  const p = run().finally(() => {
    if (inFlight.get(key) === p) inFlight.delete(key);
  });
  inFlight.set(key, p);
  return p;
}

/** Tests only. */
export function inFlightCountForTests(): number {
  return inFlight.size;
}

/** Navigation seam: tests replace `assign`. */
export const ouraNav = {
  assign(url: string): void {
    window.location.assign(url);
  },
};
