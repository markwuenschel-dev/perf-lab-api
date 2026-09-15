import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import * as api from "../api/perfLabClient";
import type { OnboardRequest, ProfileRead, UserResponse } from "../types";
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

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => getStoredToken());
  const [user, setUser] = useState<UserResponse | null>(null);
  const [profile, setProfile] = useState<ProfileRead | null>(null);
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

  const logout = useCallback(() => {
    clearStoredSession();
    setToken(null);
    setUser(null);
    setProfile(null);
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
    try {
      setProfile(await api.getProfile(t));
    } catch {
      setProfile(null);
    }
  }, []);

  const enterGuest = useCallback(() => {
    // Drop straight into onboarding (App routes on onboardingPending), exactly
    // like a fresh register — minus the account and minus any persistence.
    setIsGuest(true);
    setOnboardingPending(true);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearStoredSession();
      setToken(null);
      setUser(null);
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
      try {
        const u = await api.fetchMe(activeToken);
        if (isCancelled()) return;
        if (!isSessionUser(u)) {
          // A 200 that is not a session (proxy interstitial, HTML error page).
          setSessionUnavailable(toUnavailable(MALFORMED_SESSION_RESPONSE));
          return;
        }
        setUser(u);
        setSessionUnavailable(null);
      } catch (err) {
        if (isCancelled()) return;
        const failure = classifyAuthFailure(err);
        if (failure.action === "sign-out") {
          logout();
          return;
        }
        setSessionUnavailable(toUnavailable(failure));
        return;
      }
      // Profile is best-effort — a missing/failed load just leaves it null and
      // consumers fall back (e.g. sidebar → email local-part).
      try {
        const p = await api.getProfile(activeToken);
        if (!isCancelled()) setProfile(p);
      } catch {
        if (!isCancelled()) setProfile(null);
      }
    },
    [logout, toUnavailable],
  );

  /** Retry a session check that failed transiently, from a UI affordance. */
  const retrySession = useCallback(async () => {
    const t = getStoredToken();
    if (!t) return;
    await loadSession(t, () => false);
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

  const login = useCallback(async (emailIn: string, password: string) => {
    setIsLoading(true);
    try {
      const tr = await api.login(emailIn, password);
      setStoredToken(tr.access_token);
      setStoredEmail(emailIn);
      setEmail(emailIn);
      setIsGuest(false); // a real session supersedes any guest session
      setToken(tr.access_token);
      const u = await api.fetchMe(tr.access_token);
      setUser(u);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const register = useCallback(
    async (emailIn: string, password: string) => {
      setIsLoading(true);
      try {
        await api.register(emailIn, password);
        await login(emailIn, password);
        setOnboardingPending(true);
      } finally {
        setIsLoading(false);
      }
    },
    [login],
  );

  const completeOnboarding = useCallback(
    async (req: Partial<OnboardRequest>) => {
      // Errors propagate to the screen. This used to swallow them, which hid that the call
      // was sent without a token and every onboarding was refused: a refused onboarding
      // means the athlete's facts were NOT saved, and they must be told.
      try {
        // Guests persist nothing — skip the profile-seeding call entirely.
        if (isGuest || !token) return;
        // Identity comes from the bearer token, so OnboardRequest has no `email`.
        // The backend fills server-side defaults for any field we omit, so a
        // partial (even `{}`) is a valid payload.
        await api.onboard(req as OnboardRequest, token);
        // Pull the freshly-seeded profile so the sidebar shows the onboarded name
        // immediately. Best-effort: the onboarding itself already succeeded.
        await refreshProfile().catch(() => undefined);
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
    [token, user, profile, email, isGuest, isLoading, sessionUnavailable, retrySession, onboardingPending, login, register, completeOnboarding, refreshProfile, enterGuest, logout],
  );

  return (
    <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
  );
}
