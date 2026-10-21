// src/perflab/resource.ts
//
// The canonical resource contract for every token-gated read in the app.
//
// Two dimensions, deliberately separate:
//
//   availability      guest | loading | error | success   (is there a usable payload?)
//   refresh attempt   idle | loading | error              (only meaningful once one exists)
//
// A flat `refreshing: boolean` cannot express "the previous payload is still
// usable but the refresh failed" — the state that matters most, because the old
// hook nulled `data` on failure and screens then fell through `?? sim` to
// fixtures. Top-level `error` therefore means *no usable payload*; a failed
// refresh over good data is `success` + `refresh.error`.
//
// Pure module: no React, no fetching. The transition table lives here so it can
// be tested without a DOM; useAuthedResource only wires it to effects.

import { isStateUnavailable } from "./provenance";

export interface ResourceError {
  message: string;
  /** A machine-readable refusal code carried from the ApiError, when there is one. */
  code?: string;
}

export type RefreshState<E = ResourceError> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; error: E };

export type AuthedResource<T, E = ResourceError> =
  | { status: "guest" }
  | { status: "loading" }
  | { status: "error"; error: E }
  | { status: "success"; data: T; refresh: RefreshState<E> };

/** Events the hook feeds the transition table. */
export type ResourceEvent<T, E = ResourceError> =
  /** No token: there is no authenticated request authority at all. */
  | { type: "signed-out" }
  /**
   * The authenticated identity changed (token replaced). The previous user's
   * payload loses authority immediately — never carried across identities.
   */
  | { type: "identity-changed" }
  /** A request began for the current identity (mount, or a deps change). */
  | { type: "request-started" }
  | { type: "request-succeeded"; data: T }
  | { type: "request-failed"; error: E };

const IDLE: RefreshState<never> = { status: "idle" };

/**
 * The whole state machine. Total and pure — every event is handled for every
 * state, so a call site can never observe an in-between shape.
 */
export function reduceResource<T, E>(
  state: AuthedResource<T, E>,
  event: ResourceEvent<T, E>,
): AuthedResource<T, E> {
  switch (event.type) {
    case "signed-out":
      return { status: "guest" };

    case "identity-changed":
      // Discard the prior identity's payload rather than refreshing over it.
      return { status: "loading" };

    case "request-started":
      // Same identity: keep a usable payload on screen and mark the attempt.
      return state.status === "success"
        ? { ...state, refresh: { status: "loading" } }
        : { status: "loading" };

    case "request-succeeded":
      return { status: "success", data: event.data, refresh: IDLE };

    case "request-failed":
      // A failure only erases data when there was none worth keeping — except a coded
      // "state cannot be shown" refusal (INT-15 2C): it is permanent until repaired, and the
      // payload still on screen would present an older snapshot as current.
      if (isStateUnavailable(event.error)) return { status: "error", error: event.error };
      return state.status === "success"
        ? { ...state, refresh: { status: "error", error: event.error } }
        : { status: "error", error: event.error };

    default:
      return assertNever(event);
  }
}

/** The state a resource starts in, decided synchronously from token presence. */
export function initialResource<T, E>(hasToken: boolean): AuthedResource<T, E> {
  return hasToken ? { status: "loading" } : { status: "guest" };
}

/**
 * Branch selection, in the one fixed order every surface must follow:
 * guest → loading → error → empty → success.
 *
 * `isEmpty` is an editorial reading of *successful* data and is never consulted
 * for guest, loading or error — a failed fetch must never read as "you have
 * nothing".
 */
export type ResourceBranch<T, E = ResourceError> =
  | { kind: "guest" }
  | { kind: "loading" }
  | { kind: "error"; error: E }
  | { kind: "empty"; refresh: RefreshState<E> }
  | { kind: "success"; data: T; refresh: RefreshState<E> };

export function selectResourceBranch<T, E>(
  resource: AuthedResource<T, E>,
  isEmpty?: (data: T) => boolean,
): ResourceBranch<T, E> {
  switch (resource.status) {
    case "guest":
      return { kind: "guest" };
    case "loading":
      return { kind: "loading" };
    case "error":
      return { kind: "error", error: resource.error };
    case "success":
      return isEmpty?.(resource.data)
        ? { kind: "empty", refresh: resource.refresh }
        : { kind: "success", data: resource.data, refresh: resource.refresh };
    default:
      return assertNever(resource);
  }
}

/** True when a payload is on screen but the latest refresh attempt failed. */
export function isStale<T, E>(resource: AuthedResource<T, E>): boolean {
  return resource.status === "success" && resource.refresh.status === "error";
}

/** Narrow to a usable payload, or null. Never invents a fallback value. */
export function resourceData<T, E>(resource: AuthedResource<T, E>): T | null {
  return resource.status === "success" ? resource.data : null;
}

export function toResourceError(e: unknown): ResourceError {
  const message = (e as { message?: unknown } | null)?.message;
  const code = (e as { code?: unknown } | null)?.code;
  const out: ResourceError = { message: typeof message === "string" && message ? message : "Failed to load" };
  if (typeof code === "string" && code) out.code = code;
  return out;
}

export function assertNever(value: never): never {
  throw new Error(`Unhandled resource state: ${JSON.stringify(value)}`);
}
