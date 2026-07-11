// src/perflab/AppShell.tsx
import type { ComponentType } from "react";
import { usePerfLab } from "./store";
import type { Screen } from "./store";
import { Sidebar } from "./Sidebar";
import { Card } from "./ui";
import { OverviewScreen } from "./screens/OverviewScreen";
import { AssessmentSurfaceScreen } from "./screens/AssessmentSurfaceScreen";
import { TwinScreen } from "./screens/TwinScreen";
import { PlanningScreen } from "./screens/PlanningScreen";
import { HistoryScreen } from "./screens/HistoryScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { ObjectivesScreen } from "./screens/ObjectivesScreen";
import { SimulatorScreen } from "./screens/SimulatorScreen";
import { AuthModal } from "./overlays/AuthModal";
import { LogWorkoutModal } from "./overlays/LogWorkoutModal";
import { CheckinModal } from "./overlays/CheckinModal";
import { FeedbackModal } from "./overlays/FeedbackModal";
import { SessionPlayer } from "./overlays/SessionPlayer";
import { ExplainDrawer } from "./overlays/ExplainDrawer";
import { BlockCreateModal } from "./overlays/BlockCreateModal";
import { ObjectiveCreateModal } from "./overlays/ObjectiveCreateModal";
import { MacrocycleCreateModal } from "./overlays/MacrocycleCreateModal";

const SCREENS: Partial<Record<Screen, ComponentType>> = {
  overview: OverviewScreen,
  assess: AssessmentSurfaceScreen,
  twin: TwinScreen,
  planning: PlanningScreen,
  history: HistoryScreen,
  settings: SettingsScreen,
  objectives: ObjectivesScreen,
  simulate: SimulatorScreen,
};

const EMPTY: Partial<Record<Screen, { t: string; d: string }>> = {
  overview: { t: "Welcome to Perf Lab", d: "Your twin seeds from a provisional experience prior — assess a benchmark whenever you like to sharpen it." },
  twin: { t: "Your twin is provisional", d: "S(t) starts from an experience-level prior. Assess a benchmark to replace an estimate with a measurement." },
  planning: { t: "Planning is ready", d: "Sessions are prescribed against readiness. Assess a benchmark to raise confidence in the plan." },
  history: { t: "No history yet", d: "Once you log workouts and assess benchmarks, your trends and progressions build up here." },
};

function EmptyState({ screen }: { screen: Screen }) {
  const { actions } = usePerfLab();
  const em = EMPTY[screen] || EMPTY.overview!;
  return (
    <section className="flex min-h-[82vh] items-center justify-center px-[30px] pb-9 pt-[26px]">
      <Card className="flex max-w-[520px] flex-col items-center gap-4 p-[44px] text-center">
        <div className="grid h-[60px] w-[60px] place-items-center rounded-[16px] border border-ac/25 bg-ac/[0.1]">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="1.6"><path d="M12 2 4 7v10l8 5 8-5V7z" /><path d="M12 22V12M4 7l8 5 8-5" /></svg>
        </div>
        <div className="text-[22px] font-bold leading-[1.2] text-ink">{em.t}</div>
        <div className="max-w-[380px] text-[13.5px] font-medium leading-[1.6] text-mute">{em.d}</div>
        <button onClick={() => actions.setScreen("assess")} className="mt-[6px] rounded-[10px] bg-gradient-to-r from-ac to-[#a7e36e] px-5 py-3 text-[13px] font-semibold leading-none text-[#0a0c10]">Assess a benchmark →</button>
      </Card>
    </section>
  );
}

export function AppShell() {
  const { state } = usePerfLab();
  const { screen, fresh } = state;
  const showEmpty = fresh && screen !== "assess" && screen !== "onboarding" && screen !== "settings";
  const ScreenComp = SCREENS[screen] ?? OverviewScreen;

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="min-w-0 flex-1">{showEmpty ? <EmptyState screen={screen} /> : <ScreenComp />}</main>
      <AuthModal />
      <LogWorkoutModal />
      <CheckinModal />
      <FeedbackModal />
      <SessionPlayer />
      <ExplainDrawer />
      <BlockCreateModal />
      <ObjectiveCreateModal />
      <MacrocycleCreateModal />
    </div>
  );
}
