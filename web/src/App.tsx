// src/App.tsx
//
// Perf Lab "Performance OS" — full client-side port of the design prototype.
// Login is a hard gate: until the user is authenticated, App renders the
// full-screen LoginScreen and nothing else. Once authenticated, onboarding is a
// full-screen takeover; every other screen renders inside the app shell
// (sidebar + main + overlays). State lives in <PerfLabProvider> (see main.tsx);
// auth lives in <AuthProvider>. A 401 on any /v1 call clears the token and
// bounces the user back to the gate.
import { useEffect } from "react";
import { useAuth } from "./auth/useAuth";
import { usePerfLab } from "./perflab/store";
import { AppShell } from "./perflab/AppShell";
import { LoginScreen } from "./perflab/screens/LoginScreen";
import { OnboardingScreen } from "./perflab/screens/OnboardingScreen";

export default function App() {
  const { state, actions } = usePerfLab();
  const { isAuthenticated, onboardingPending } = useAuth();

  // After register, auth flips onboardingPending → drop the new user into the
  // onboarding takeover. `actions` is a stable useMemo, so this only fires on
  // the pending transition (and again — as a no-op — once it clears).
  useEffect(() => {
    if (onboardingPending) actions.setScreen("onboarding");
  }, [onboardingPending, actions]);

  if (!isAuthenticated) return <LoginScreen />;

  return state.screen === "onboarding" ? <OnboardingScreen /> : <AppShell />;
}
