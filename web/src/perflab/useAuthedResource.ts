// src/perflab/useAuthedResource.ts
//
// Data-fetching hook for screens that pull live data from the token-gated
// backend. It owns *authentication and acquisition* state only — see
// `resource.ts` for the contract it returns:
//
//   guest      no token, so no request authority at all (never a request)
//   loading    a token exists and there is no usable payload yet
//   error      the request failed and nothing usable is on screen
//   success    a usable payload, plus a nested refresh attempt state
//
// "Empty" is deliberately NOT a state here: whether zero rows means "you have
// logged nothing yet" is an editorial reading of successful data, decided at the
// renderer via an `isEmpty` predicate (see ResourceState / selectResourceBranch).
//
// A 401 inside the fetcher already clears the session via perfLabClient's
// sessionOn401 path.
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/auth/useAuth";
import {
  initialResource,
  reduceResource,
  toResourceError,
  type AuthedResource,
} from "./resource";

export type { AuthedResource } from "./resource";

export type AuthedResourceOptions = {
  /**
   * B1: the response depends on which day the server counts as the athlete's today (readiness,
   * today's session, missed statuses, countdowns...). Such a resource is keyed on the ONE
   * calendar identity (`useAuth().calendarKey`): it does not fetch while the calendar is still
   * pending (the profile and any zone capture have not settled), and a confirmed change of
   * calendar discards the payload and refetches, so a response fetched under the obsolete
   * calendar is never shown — not even one still in flight.
   */
  calendar?: boolean;
};

export function useAuthedResource<T>(
  fetcher: (token: string) => Promise<T>,
  deps: unknown[] = [],
  options: AuthedResourceOptions = {},
): AuthedResource<T> {
  const { token, calendarKey } = useAuth();
  // `calendarKey` is absent only where a test double of the auth context omits it.
  const calendar = options.calendar ? (calendarKey ?? "") : "";
  const waiting = calendar === "pending";
  const [state, setState] = useState<AuthedResource<T>>(() => initialResource(token != null));

  // Identity is adjusted during render, not in an effect: a token that arrives
  // must read as `loading` on the very first commit (the old hook first-rendered
  // with loading:false, which is what forced every screen's
  // `loading || data === null` dance and flashed empty states for one frame).
  // A replaced token discards the previous user's payload in the same commit —
  // an effect would leave one frame of the wrong athlete's data on screen.
  // A calendar change is an identity change too (B1): the old payload belongs to another day.
  const key = `${token ?? ""}|${calendar}`;
  const [identity, setIdentity] = useState(key);
  if (identity !== key) {
    setIdentity(key);
    setState(initialResource(token != null));
  }

  // Keep the latest fetcher without making it a dep — `deps` drives re-fetch.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (!token) {
      setState((s) => (s.status === "guest" ? s : { status: "guest" }));
      return;
    }
    // The calendar is not settled yet: stay `loading`, ask nothing (the key's change refetches).
    if (waiting) return;

    // Request authority: React runs this effect's cleanup before the next effect
    // (token replaced, deps changed) and on unmount, so `cancelled` is what stops
    // an obsolete or slower response from overwriting a newer state. A second
    // generation counter would be redundant — and untestable, since no behavior
    // distinguishes it from this flag.
    let cancelled = false;
    const live = () => !cancelled;

    // Same identity + existing payload => refresh (data stays on screen).
    // Otherwise this is a first load and the resource is already `loading`.
    setState((s) => reduceResource(s, { type: "request-started" }));

    fetcherRef.current(token).then(
      (data) => {
        if (live()) setState((s) => reduceResource(s, { type: "request-succeeded", data }));
      },
      (e: unknown) => {
        if (live()) setState((s) => reduceResource(s, { type: "request-failed", error: toResourceError(e) }));
      },
    );

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, calendar, ...deps]);

  return state;
}

// The B3 migration shim that used to live here is GONE. It existed so that
// intermediate commits still built while the 25 call sites moved onto the
// canonical contract; every consumer has now migrated, and the ratchet in
// resourceMigration.test.ts fails if the symbol or its `{data, loading, error}`
// shape is ever reintroduced.
