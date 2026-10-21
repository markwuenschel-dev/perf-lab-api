import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import * as api from "../api/perfLabClient";
import { browserTimezone, calendarKeyOf, shouldCaptureTimezone } from "../perflab/athleteTimezone";
import type { OnboardRequest, OnboardResponse, ProfileRead, UserResponse } from "../types";
import {
  classifyAuthFailure,
  describeSessionUnavailable,
  isSessionUser,
  MALFORMED_SESSION_RESPONSE,
  type SessionPreserved,
} from "./authFailure";
import {
  AuthContext,
  type AuthContextValue,
  type SessionUnavailable,
} from "./perfLabAuthContext";
import { setUnauthorizedHandler } from "./sessionBridge";
import {
  clearStoredSession,
  getStoredEmail,
  getStoredToken,
  setStoredEmail,
  setStoredToken,
} from "./tokenStorage";

/** How long the session check (/auth/me) and the profile load may each take before they count as
 *  failed (B1): a hung request must not leave every day-dependent screen loading with no way to
 *  retry. */
export const PROFILE_LOAD_TIMEOUT_MS = 8000;

/**
 * `run`'s result, or a rejection once `ms` pass; the request is aborted too. The rejection is a
 * `TypeError`, what `fetch` throws when no response arrives, so the session-failure policy reads
 * a timeout as a network failure: the session is kept and the check is retryable.
 */
async function within<T>(ms: number, run: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const abort = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      abort.abort();
      reject(new TypeError("request timed out"));
    }, ms);
  });
  try {
    return await Promise.race([run(abort.signal), timeout]);
  } finally {
    clearTimeout(timer);
  }
}

const loadProfileWithin = (token: string, ms: number) =>
  within(ms, (signal) => api.getProfile(token, signal));

export function AuthProvider({
  children,
  profileTimeoutMs = PROFILE_LOAD_TIMEOUT_MS,
}: {
  children: ReactNode;
  profileTimeoutMs?: number;
}) {
  const [token, setToken] = useState<string | null>(() => getStoredToken());
  const [user, setUser] = useState<UserResponse | null>(null);
  const [profile, setProfile] = useState<ProfileRead | null>(null);
  // B1: false while the profile load and any zone capture are in flight (see calendarKeyOf).
  const [calendarSettled, setCalendarSettled] = useState(false);
  const [email, setEmail] = useState(() => getStoredEmail() ?? "");
  const [isLoading, setIsLoading] = useState(false);
  const [onboardingPending, setOnboardingPending] = useState(false);
  // Guest ("try it") session: no token is ever issued, so every token-gated
  // backend write stays a no-op and nothing is persisted. It only unlocks the
  // login gate so a curious user can onboard and play with the local twin.
  const [isGuest, setIsGuest] = useState(false);
  // Set when GET /auth/me could not be confirmed for a reason that is NOT an
  // expired login. The token stays put; see ./authFailure for the policy.
  const [sessionUnavailable, setSessionUnavailable] =
    useState<SessionUnavailable | null>(null);

  // Bumped by every login and logout: a login's late answers are dropped once another login or
  // a logout has happened since it started.
  const loginGeneration = useRef(0);

  const logout = useCallback(() => {
    loginGeneration.current += 1;
    clearStoredSession();
    setToken(null);
    setUser(null);
    setProfile(null);
    setCalendarSettled(false);
    setIsGuest(false);
    setSessionUnavailable(null);
  }, []);

  // Load the athlete profile for the current token. Exposed so consumers can
  // force a re-fetch (e.g. the sidebar name after a Settings save).
  const refreshProfile = useCallback(async () => {
    const t = getStoredToken();
    if (!t) {
      setProfile(null);
      return;
    }
    // Bound to the session it started in: after a logout or another athlete's login, this
    // answer is about someone else and must not touch the state.
    const stale = () => getStoredToken() !== t;
    try {
      const p = await loadProfileWithin(t, profileTimeoutMs);
      if (stale()) return;
      setProfile(p);
    } catch {
      // A failed refresh is not news about the zone: keep the last confirmed profile (and with
      // it the calendar). With none confirmed yet, the calendar stays "unavailable".
      if (stale()) return;
    }
    setCalendarSettled(true);
  }, [profileTimeoutMs]);

  const enterGuest = useCallback(() => {
    // Drop straight into onboarding (App routes on onboardingPending), exactly
    // like a fresh register — minus the account and minus any persistence.
    setIsGuest(true);
    setOnboardingPending(true);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler((requestToken) => {
      // A 401 refuses the token its request was sent with. A late one for an earlier session's
      // token says nothing about the current session, which stands.
      if (getStoredToken() !== requestToken) return;
      clearStoredSession();
      setToken(null);
      setUser(null);
      setCalendarSettled(false);
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const toUnavailable = useCallback(
    (failure: SessionPreserved) => ({
      reason: failure.reason,
      status: failure.status,
      message: describeSessionUnavailable(failure.reason),
    }),
    [],
  );

  /**
   * Confirm the stored session against the backend.
   *
   * The ONLY outcome that clears the session is a positively-identified 401.
   * Any other failure — 5xx, other 4xx, offline/CORS `TypeError`, unparseable
   * payload, anything unrecognised — keeps the token and raises a retryable
   * `sessionUnavailable`, because none of those prove the athlete is logged out.
   */
  const loadSession = useCallback(
    async (activeToken: string, isCancelled: () => boolean) => {
      setCalendarSettled(false);
      // Whatever happens below, the calendar is settled once this load is done with it.
      const settle = () => {
        if (!isCancelled()) setCalendarSettled(true);
      };
      try {
        const u = await within(profileTimeoutMs, (signal) => api.fetchMe(activeToken, signal));
        if (isCancelled()) return;
        if (!isSessionUser(u)) {
          // A 200 that is not a session (proxy interstitial, HTML error page).
          setSessionUnavailable(toUnavailable(MALFORMED_SESSION_RESPONSE));
          settle();
          return;
        }
        setUser(u);
        setSessionUnavailable(null);
      } catch (err) {
        if (isCancelled()) return;
        const failure = classifyAuthFailure(err);
        if (failure.action === "sign-out") {
          // A 401 refuses the token this load used. `isCancelled` flips only in the effect's
          // cleanup, after React commits a newer token, while login stores that token at once:
          // in that gap an earlier session's 401 must not sign out the new session.
          if (getStoredToken() === activeToken) logout();
          return;
        }
        setSessionUnavailable(toUnavailable(failure));
        settle();
        return;
      }
      // Profile is best-effort — a missing/failed load just leaves it null and
      // consumers fall back (e.g. sidebar → email local-part).
      let p: ProfileRead;
      try {
        p = await loadProfileWithin(activeToken, profileTimeoutMs);
        if (isCancelled()) return;
        setProfile(p);
      } catch {
        if (!isCancelled()) setProfile(null);
        settle();
        return;
      }
      // The profile is the server's word on the zone ("unknown" included): the calendar is
      // settled now, and day-dependent data loads. The capture below is optional and never
      // blocks it; when it succeeds, the confirmed zone changes the key and the data refetches.
      settle();
      // B1: the athlete's timezone is captured from this browser only while it is unknown. A
      // known zone (captured before, or chosen in Settings) is never offered again, and the
      // server refuses to replace it anyway. Best-effort: a failure leaves it unknown (UTC).
      const zone = browserTimezone();
      if (zone && shouldCaptureTimezone(p)) {
        try {
          const captured = await api.captureDetectedTimezone({ timezone: zone }, activeToken);
          if (!isCancelled()) setProfile(captured);
        } catch {
          /* stays unknown; the next session load offers it again */
        }
      }
    },
    [logout, toUnavailable, profileTimeoutMs],
  );

  /** Retry a session check that failed transiently, from a UI affordance. */
  const retrySession = useCallback(async () => {
    const t = getStoredToken();
    if (!t) return;
    // Cancelled the moment the session changes: a retry's late answers belong to the session
    // it was started for, never to the next one.
    await loadSession(t, () => getStoredToken() !== t);
  }, [loadSession]);

  useEffect(() => {
    if (!token) {
      setUser(null);
      setProfile(null);
      setSessionUnavailable(null);
      return;
    }
    let cancelled = false;
    void loadSession(token, () => cancelled);
    return () => {
      cancelled = true;
    };
  }, [token, loadSession]);

  /** Resolves true when this login established the session, false when a logout or a newer
   *  login superseded it (its answers then changed nothing). */
  const login = useCallback(async (emailIn: string, password: string): Promise<boolean> => {
    const generation = ++loginGeneration.current;
    setIsLoading(true);
    try {
      const tr = await api.login(emailIn, password);
      // A logout or another login since this one started: this answer is not the session now.
      if (loginGeneration.current !== generation) return false;
      setStoredToken(tr.access_token);
      setStoredEmail(emailIn);
      setEmail(emailIn);
      setIsGuest(false); // a real session supersedes any guest session
      setCalendarSettled(false); // the new session's calendar is not known until its profile is
      setToken(tr.access_token);
      // Bounded like the session load's check: a hung /auth/me rejects (aborted) instead of
      // leaving the spinner up. Nothing is written on failure; the token is stored, so the
      // session load for it (the effect above) owns the retryable `sessionUnavailable` state.
      const u = await within(profileTimeoutMs, (signal) => api.fetchMe(tr.access_token, signal));
      // Bound to this login's session: after a logout or a newer login it must not touch state.
      if (loginGeneration.current !== generation || getStoredToken() !== tr.access_token) {
        return false;
      }
      setUser(u);
      return true;
    } finally {
      setIsLoading(false);
    }
  }, [profileTimeoutMs]);

  const register = useCallback(
    async (emailIn: string, password: string) => {
      setIsLoading(true);
      try {
        await api.register(emailIn, password);
        // Onboarding belongs to the session this register created: not to one a logout or a
        // newer login put in its place.
        if (await login(emailIn, password)) setOnboardingPending(true);
      } finally {
        setIsLoading(false);
      }
    },
    [login],
  );

  const completeOnboarding = useCallback(
    async (req: Partial<OnboardRequest>): Promise<OnboardResponse | undefined> => {
      // Errors propagate to the screen. This used to swallow them, which hid that the call
      // was sent without a token and every onboarding was refused: a refused onboarding
      // means the athlete's facts were NOT saved, and they must be told.
      try {
        // Guests persist nothing — skip the profile-seeding call entirely.
        if (isGuest || !token) return undefined;
        // Identity comes from the bearer token, so OnboardRequest has no `email`.
        // The backend fills server-side defaults for any field we omit, so a
        // partial (even `{}`) is a valid payload.
        const response = await api.onboard(req as OnboardRequest, token);
        // Pull the freshly-seeded profile so the sidebar shows the onboarded name
        // immediately. Best-effort: the onboarding itself already succeeded.
        await refreshProfile().catch(() => undefined);
        // The screen reads what happened to each strength report (P4-3).
        return response;
      } finally {
        setOnboardingPending(false);
      }
    },
    [isGuest, token, refreshProfile],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      token,
      user,
      profile,
      calendarKey: calendarKeyOf(calendarSettled, profile),
      email,
      setEmail,
      isAuthenticated: Boolean(token),
      isGuest,
      isLoading,
      sessionUnavailable,
      retrySession,
      onboardingPending,
      login,
      register,
      completeOnboarding,
      refreshProfile,
      enterGuest,
      logout,
    }),
    [token, user, profile, calendarSettled, email, isGuest, isLoading, sessionUnavailable, retrySession, onboardingPending, login, register, completeOnboarding, refreshProfile, enterGuest, logout],
  );

  return (
    <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
  );
}
