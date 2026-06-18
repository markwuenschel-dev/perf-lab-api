// src/App.tsx
//
// Perf Lab "Performance OS" — full client-side port of the design prototype.
// Onboarding is a full-screen takeover; every other screen renders inside the
// app shell (sidebar + main + overlays). State lives in <PerfLabProvider>
// (see main.tsx). The previous backend-wired UI is parked under src/ (auth/,
// api/, components/) pending a follow-up that re-wires real endpoints.
import { usePerfLab } from "./perflab/store";
import { AppShell } from "./perflab/AppShell";
import { OnboardingScreen } from "./perflab/screens/OnboardingScreen";

export default function App() {
  const { state } = usePerfLab();
  return state.screen === "onboarding" ? <OnboardingScreen /> : <AppShell />;
}
