// src/App.tsx
import { useState } from "react";
import { motion } from "framer-motion";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AuthStrip } from "./components/AuthStrip";
import { DigitalTwinPanel } from "./components/DigitalTwinPanel";
import { EngineExplainer } from "./components/EngineExplainer";
import { HeroFlowColumn } from "./components/HeroFlowColumn";
import { OnboardingForm } from "./components/OnboardingForm";
import { PlanningPanel } from "./components/PlanningPanel";
import { useAuth } from "./auth/useAuth";
import type { FieldTestHandoff } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL as string;

type MainTab = "field" | "twin" | "planning";

export default function App() {
  const year = new Date().getFullYear();
  const [mainTab, setMainTab] = useState<MainTab>("field");
  const [fieldTestHandoff, setFieldTestHandoff] = useState<FieldTestHandoff | null>(null);
  const { isAuthenticated, onboardingPending } = useAuth();

  function handleSendToTwin(handoff: FieldTestHandoff) {
    setFieldTestHandoff(handoff);
    setMainTab("twin");
  }

  if (isAuthenticated && onboardingPending) {
    return <OnboardingForm />;
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      {/* Neon grid background (very subtle) */}
      <div className="fixed inset-0 bg-[radial-gradient(#00f5ff_0.8px,transparent_0.8px)] bg-[length:40px_40px] opacity-[0.03] pointer-events-none" />

      <header className="sticky top-0 z-50 border-b border-white/10 bg-zinc-950/80 backdrop-blur-xl">
        <div className="mx-auto max-w-screen-2xl px-6 py-6">
          <motion.div
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-6"
          >
            {/* Logo + headline */}
            <div className="space-y-4">
              <div className="inline-flex items-center gap-2 rounded-3xl border border-neon-cyan/30 bg-black/40 px-4 py-1.5 text-xs font-bold uppercase tracking-[1.5px] text-neon-cyan shadow-[0_0_20px_-4px] shadow-neon-cyan">
                <div className="h-2 w-2 animate-pulse rounded-full bg-neon-cyan" />
                PERF LAB • LIVE
              </div>

              <h1 className="max-w-xl text-5xl font-semibold tracking-tighter lg:text-6xl">
                Field test →{" "}
                <span className="bg-gradient-to-r from-neon-cyan via-neon-magenta to-neon-violet bg-clip-text text-transparent">
                  Digital Twin
                </span>
              </h1>

              <p className="max-w-md text-lg text-zinc-200">
                300 m + 1.5 mi → VO₂ • fatigue • S(t) state • adaptive prescription
              </p>
            </div>

            <AuthStrip />
          </motion.div>
        </div>

        {/* Premium tabs */}
        <div className="mx-auto max-w-screen-2xl px-6">
          <Tabs value={mainTab} onValueChange={(v) => setMainTab(v as MainTab)}>
            <TabsList className="grid w-full max-w-xl grid-cols-3 bg-zinc-900 border border-white/10">
              <TabsTrigger value="field" className="data-[state=active]:bg-neon-cyan data-[state=active]:text-black">
                FIELD TEST
              </TabsTrigger>
              <TabsTrigger value="twin" className="data-[state=active]:bg-neon-cyan data-[state=active]:text-black">
                DIGITAL TWIN
              </TabsTrigger>
              <TabsTrigger value="planning" className="data-[state=active]:bg-neon-cyan data-[state=active]:text-black">
                PLANNING
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </header>

      <main className="mx-auto max-w-screen-2xl px-6 pb-12">
        <EngineExplainer />

        <motion.div
          key={mainTab}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="mt-8"
        >
          {mainTab === "field" && <HeroFlowColumn onSendToTwin={handleSendToTwin} />}
          {mainTab === "twin" && (
            <DigitalTwinPanel
              handoff={fieldTestHandoff}
              onHandoffConsumed={() => setFieldTestHandoff(null)}
            />
          )}
          {mainTab === "planning" && <PlanningPanel />}
        </motion.div>
      </main>

      {/* Footer */}
      <footer className="border-t border-white/10 bg-black/40 py-8 text-xs text-zinc-300">
        <div className="mx-auto max-w-screen-2xl px-6 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-6">
            <span className="font-medium text-zinc-200">perf-lab-web</span>
            <span className="text-zinc-400">© {year}</span>
            <span className="text-zinc-400">Built by Nalakram • React + FastAPI</span>
          </div>

          <div className="font-mono text-[10px] text-zinc-400">
            {API_BASE}
            {import.meta.env.DEV && <span className="ml-2 text-neon-cyan">• DEV</span>}
          </div>
        </div>
      </footer>
    </div>
  );
}
