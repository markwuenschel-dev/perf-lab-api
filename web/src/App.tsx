// src/App.tsx
//
// Perf Lab "Performance OS" — full client-side port of the design prototype.
// Onboarding is a full-screen takeover; every other screen renders inside the
// app shell (sidebar + main + overlays). State lives in <PerfLabProvider>
// (see main.tsx). The previous backend-wired UI is parked under src/ (auth/,
// api/, components/) pending a follow-up that re-wires real endpoints.
import { useEffect } from "react";
import { useAuth } from "./auth/useAuth";
import { usePerfLab } from "./perflab/store";
import { AppShell } from "./perflab/AppShell";
import { OnboardingScreen } from "./perflab/screens/OnboardingScreen";

export default function App() {
  const { state, actions } = usePerfLab();
  const { onboardingPending } = useAuth();

  // After register, auth flips onboardingPending → drop the new user into the
  // onboarding takeover. `actions` is a stable useMemo, so this only fires on
  // the pending transition (and again — as a no-op — once it clears).
  useEffect(() => {
    if (onboardingPending) actions.setScreen("onboarding");
  }, [onboardingPending, actions]);

  return state.screen === "onboarding" ? <OnboardingScreen /> : <AppShell />;
}
