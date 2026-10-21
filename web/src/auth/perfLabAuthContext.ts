import { createContext } from "react";

import type { OnboardRequest, OnboardResponse, ProfileRead, UserResponse } from "../types";
import type { CalendarKey } from "../perflab/athleteTimezone";
import type { SessionUnavailableReason } from "./authFailure";

/**
 * The session is still valid and still stored — we just could not confirm it
 * with the backend. Surfaced so the UI can say "we can't reach the server"
 * and offer a retry, instead of silently ejecting the athlete.
 */
export type SessionUnavailable = {
  reason: SessionUnavailableReason;
  /** HTTP status when there was a response; `null` for network/offline/CORS. */
  status: number | null;
  /** Ready-to-render explanation (see `describeSessionUnavailable`). */
  message: string;
};

export type AuthContextValue = {
  token: string | null;
  user: UserResponse | null;
  /** Backend athlete profile (GET /v1/profile), loaded once per session and
   *  refreshed on demand via `refreshProfile()` (e.g. after a Settings save). */
  profile: ProfileRead | null;
  /** B1: the calendar identity day-dependent resources are keyed on (see `calendarKeyOf`). It
   *  changes only when the server confirms a profile: after the load and any zone capture
   *  settle, and on every refresh. */
  calendarKey: CalendarKey;
  email: string;
  setEmail: (e: string) => void;
  isAuthenticated: boolean;
  /** Local-only "try it" session: no token, nothing is persisted. */
  isGuest: boolean;
  isLoading: boolean;
  /**
   * Non-null when the last session check failed for a reason that is NOT an
   * expired login. The token is deliberately KEPT — a deploy, a restart or a
   * dropped connection must not sign anyone out. `null` whenever the session
   * is confirmed (or when there is no session at all).
   */
  sessionUnavailable: SessionUnavailable | null;
  /** Re-attempt the session check. Clears `sessionUnavailable` on success. */
  retrySession: () => Promise<void>;
  onboardingPending: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  /** Resolves to the server's response — including what happened to each strength report — or
   *  to undefined for a guest, whose onboarding sends nothing. */
  completeOnboarding: (req: Partial<OnboardRequest>) => Promise<OnboardResponse | undefined>;
  /** Re-fetch the athlete profile so consumers (e.g. the sidebar) see edits live. */
  refreshProfile: () => Promise<void>;
  /** Enter a guest session and drop into onboarding. */
  enterGuest: () => void;
  logout: () => void;
};

export const AuthContext = createContext<AuthContextValue | null>(null);
