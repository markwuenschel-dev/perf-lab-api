import { createContext } from "react";

import type { OnboardRequest, UserResponse } from "../types";

export type AuthContextValue = {
  token: string | null;
  user: UserResponse | null;
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
  /** Enter a guest session and drop into onboarding. */
  enterGuest: () => void;
  logout: () => void;
};

export const AuthContext = createContext<AuthContextValue | null>(null);
