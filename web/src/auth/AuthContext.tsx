import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import * as api from "../api/perfLabClient";
import type { OnboardRequest, ProfileRead, UserResponse } from "../types";
import { AuthContext, type AuthContextValue } from "./perfLabAuthContext";
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

  const logout = useCallback(() => {
    clearStoredSession();
    setToken(null);
    setUser(null);
    setProfile(null);
    setIsGuest(false);
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

  useEffect(() => {
    if (!token) {
      setUser(null);
      setProfile(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const u = await api.fetchMe(token);
        if (!cancelled) setUser(u);
      } catch {
        if (!cancelled) logout();
        return;
      }
      // Profile is best-effort — a missing/failed load just leaves it null and
      // consumers fall back (e.g. sidebar → email local-part).
      try {
        const p = await api.getProfile(token);
        if (!cancelled) setProfile(p);
      } catch {
        if (!cancelled) setProfile(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, logout]);

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
      try {
        // Guests persist nothing — skip the profile-seeding call entirely.
        if (isGuest) return;
        // Identity comes from the auth token, so OnboardRequest has no `email`.
        // The backend fills server-side defaults for any field we omit, so a
        // partial (even `{}`) is a valid payload.
        await api.onboard(req as OnboardRequest);
        // Pull the freshly-seeded profile so the sidebar shows the onboarded
        // name immediately (not just the email fallback until a reload).
        await refreshProfile();
      } catch {
        // Best-effort: baseline state seeds on first /next-session anyway
      } finally {
        setOnboardingPending(false);
      }
    },
    [isGuest, refreshProfile],
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
      onboardingPending,
      login,
      register,
      completeOnboarding,
      refreshProfile,
      enterGuest,
      logout,
    }),
    [token, user, profile, email, isGuest, isLoading, onboardingPending, login, register, completeOnboarding, refreshProfile, enterGuest, logout],
  );

  return (
    <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
  );
}
