import { createContext } from "react";

import type { OnboardRequest, ProfileRead, UserResponse } from "../types";

export type AuthContextValue = {
  token: string | null;
  user: UserResponse | null;
  /** Backend athlete profile (GET /v1/profile), loaded once per session and
   *  refreshed on demand via `refreshProfile()` (e.g. after a Settings save). */
  profile: ProfileRead | null;
  email: string;
  setEmail: (e: string) => void;
  isAuthenticated: boolean;
  /** Local-only "try it" session: no token, nothing is persisted. */
  isGuest: boolean;
  isLoading: boolean;
  onboardingPending: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  completeOnboarding: (req: Partial<OnboardRequest>) => Promise<void>;
  /** Re-fetch the athlete profile so consumers (e.g. the sidebar) see edits live. */
  refreshProfile: () => Promise<void>;
  /** Enter a guest session and drop into onboarding. */
  enterGuest: () => void;
  logout: () => void;
};

export const AuthContext = createContext<AuthContextValue | null>(null);
