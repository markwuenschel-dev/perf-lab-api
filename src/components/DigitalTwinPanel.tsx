// src/components/DigitalTwinPanel.tsx
import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Card, CardContent } from "@/components/ui/card";

import { useAuth } from "../auth/useAuth";
import {
  getNextSession,
  getTodayPlannedSession,
  logWorkout as logDtWorkout,
  simulateDose,
} from "../api/perfLabClient";

import type {
  ApiError,
  FieldTestHandoff,
  PlannedSessionRead,
  StressDose,
  UnifiedStateVector,
  WorkoutLog,
  WorkoutPrescription,
} from "../types";
import type { TrainingGoalValue } from "../trainingGoals";

import { LogWorkoutForm } from "./twin/LogWorkoutForm";
import { NextSessionCard } from "./twin/NextSessionCard";
import { PatternPreviewDemo } from "./twin/PatternPreviewDemo";
import { StateSnapshot } from "./twin/StateSnapshot";
import { TwinConsoleHeader } from "./twin/TwinConsoleHeader";
import { TwinSummaryStrip } from "./twin/TwinSummaryStrip";

import {
  nowIso,
  readinessScore,
  toApiError,
  toApiWorkoutLog,
} from "./twin/stateUtils";

const DEFAULT_DT_LOG: WorkoutLog = {
  timestamp: nowIso(),
  modality: "Strength",
  duration_minutes: 45,
  session_rpe: 7,
  sleep_quality: 5,
  life_stress_inverse: 5,
  avg_rir: 2,
  dominant_movement_pattern: "mixed",
  novelty: 1,
};

type DigitalTwinPanelProps = {
  handoff: FieldTestHandoff | null;
  onHandoffConsumed: () => void;
};

export function DigitalTwinPanel({ handoff, onHandoffConsumed }: DigitalTwinPanelProps) {
  const { token, isAuthenticated: signedIn } = useAuth();

  const [dtGoal, setDtGoal] = useState<TrainingGoalValue>("Strength");
  const [handoffSummary, setHandoffSummary] = useState<string | null>(null);
  const [dtLog, setDtLog] = useState<WorkoutLog>(DEFAULT_DT_LOG);
  const [dtState, setDtState] = useState<UnifiedStateVector | null>(null);
  const [dtRx, setDtRx] = useState<WorkoutPrescription | null>(null);
  const [dtDose, setDtDose] = useState<StressDose | null>(null);
  const [todaySession, setTodaySession] = useState<PlannedSessionRead | null>(null);
  const [dtLoading, setDtLoading] = useState(false);
  const [dtRxLoading, setDtRxLoading] = useState(false);
  const [dtError, setDtError] = useState<ApiError | null>(null);
  const [benchmarkKey, setBenchmarkKey] = useState("periodic_retest");
  const [benchmarkValue, setBenchmarkValue] = useState<string>("");

  const prevModalityRef = useRef(dtLog.modality);

  useEffect(() => {
    if (
      dtLog.modality === "Running" &&
      prevModalityRef.current !== "Running" &&
      dtLog.dominant_movement_pattern === "mixed"
    ) {
      setDtLog((prev) => ({ ...prev, dominant_movement_pattern: "run" }));
    }
    prevModalityRef.current = dtLog.modality;
  }, [dtLog.modality, dtLog.dominant_movement_pattern]);

  // Field Test → Twin handoff: prefill the log form once, then clear the handoff.
  useEffect(() => {
    if (!handoff) return;
    setDtLog((prev) => ({ ...prev, ...handoff.log, timestamp: nowIso() }));
    setHandoffSummary(handoff.summary);
    onHandoffConsumed();
  }, [handoff, onHandoffConsumed]);

  async function refreshTwinContext(goal: string, authToken: string): Promise<void> {
    const [rx, today] = await Promise.all([
      getNextSession(goal, authToken),
      getTodayPlannedSession(goal, authToken),
    ]);
    setDtRx(rx);
    setTodaySession(today.session);
  }

  useEffect(() => {
    let cancelled = false;
    const loadRx = async () => {
      if (!token) {
        setDtRx(null);
        setTodaySession(null);
        setDtRxLoading(false);
        return;
      }
      setDtRxLoading(true);
      setDtError(null);
      try {
        const [rx, today] = await Promise.all([
          getNextSession(dtGoal, token),
          getTodayPlannedSession(dtGoal, token),
        ]);
        if (!cancelled) {
          setDtRx(rx);
          setTodaySession(today.session);
        }
      } catch {
        // graceful fallback when planning endpoint is unavailable
        try {
          const rx = await getNextSession(dtGoal, token);
          if (!cancelled) setDtRx(rx);
        } catch (err2: unknown) {
          if (!cancelled) setDtError(toApiError(err2));
        }
      } finally {
        if (!cancelled) setDtRxLoading(false);
      }
    };
    void loadRx();
    return () => {
      cancelled = true;
    };
  }, [dtGoal, token]);

  useEffect(() => {
    if (!todaySession) return;
    setDtLog((prev) => ({
      ...prev,
      planned_session_id: todaySession.id,
      is_benchmark: todaySession.is_benchmark,
    }));
    if (!todaySession.is_benchmark) {
      setBenchmarkValue("");
    }
  }, [todaySession]);

  function updateDtLog(field: keyof WorkoutLog, value: unknown) {
    setDtLog((prev) => ({ ...prev, [field]: value }));
  }

  async function handleDtLog(e?: React.FormEvent) {
    if (e) e.preventDefault();
    if (!token) return;
    setDtLoading(true);
    setDtError(null);
    setDtDose(null);
    try {
      const payload = toApiWorkoutLog({
        ...dtLog,
        timestamp: nowIso(),
        planned_session_id: todaySession?.id,
        is_benchmark: Boolean(todaySession?.is_benchmark && dtLog.is_benchmark),
        benchmark_results:
          todaySession?.is_benchmark &&
          dtLog.is_benchmark &&
          benchmarkValue.trim() !== ""
            ? { [benchmarkKey || "periodic_retest"]: Number(benchmarkValue) }
            : undefined,
      });
      const newState = await logDtWorkout(payload, token);
      setDtState(newState);
      await refreshTwinContext(dtGoal, token);
    } catch (err: unknown) {
      setDtError(toApiError(err));
    } finally {
      setDtLoading(false);
    }
  }

  async function handleDtSimulate() {
    setDtError(null);
    setDtDose(null);
    try {
      const dose = await simulateDose(toApiWorkoutLog({ ...dtLog, timestamp: nowIso() }));
      setDtDose(dose);
    } catch (err: unknown) {
      setDtError(toApiError(err));
    }
  }

  async function handleDtCrash() {
    if (!token) return;
    setDtLoading(true);
    setDtError(null);
    setDtDose(null);
    const crash: WorkoutLog = {
      timestamp: nowIso(),
      modality: "Strength",
      duration_minutes: 90,
      session_rpe: 10,
      sleep_quality: 2,
      life_stress_inverse: 2,
      avg_rir: 0,
      dominant_movement_pattern: "mixed",
      novelty: 1,
      planned_session_id: todaySession?.id,
    };
    try {
      const newState = await logDtWorkout(toApiWorkoutLog(crash), token);
      setDtState(newState);
      await refreshTwinContext(dtGoal, token);
    } catch (err: unknown) {
      setDtError(toApiError(err));
    } finally {
      setDtLoading(false);
    }
  }

  async function handleDtRefreshRx() {
    if (!token) return;
    setDtRxLoading(true);
    setDtError(null);
    try {
      await refreshTwinContext(dtGoal, token);
    } catch (err: unknown) {
      setDtError(toApiError(err));
    } finally {
      setDtRxLoading(false);
    }
  }

  const readiness =
    dtState != null && dtState.fatigue_f && dtState.tissue_t
      ? readinessScore(dtState)
      : "—";

  return (
    <div className="relative min-h-[calc(100vh-200px)] bg-black">
      <div className="absolute inset-0 bg-[repeating-linear-gradient(0deg,#00f5ff10_0px,#00f5ff10_1px,transparent_1px,transparent_4px)] pointer-events-none opacity-30" />

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="relative z-10 space-y-8 p-6"
      >
        <Card className="border border-neon-cyan/40 bg-zinc-950/90 backdrop-blur-3xl shadow-2xl shadow-neon-cyan/20 overflow-hidden">
          <TwinConsoleHeader
            dtGoal={dtGoal}
            onGoalChange={setDtGoal}
            onRefreshRx={handleDtRefreshRx}
            token={token}
          />

          <TwinSummaryStrip readiness={readiness} dtState={dtState} dtRx={dtRx} />

          {handoffSummary && (
            <div className="mx-6 mb-4 flex items-center justify-between gap-4 rounded-2xl border border-neon-cyan/40 bg-neon-cyan/5 p-4 text-sm text-neon-cyan">
              <span>Prefilled from {handoffSummary}. Review the log below, then Log &amp; Update S(t).</span>
              <button
                type="button"
                onClick={() => setHandoffSummary(null)}
                className="shrink-0 text-xs text-zinc-400 hover:text-white"
              >
                Dismiss
              </button>
            </div>
          )}

          {dtError && (
            <div className="mx-6 mb-4 rounded-2xl border border-rose-400/60 bg-rose-950/60 p-4 text-sm text-rose-200 font-mono font-medium">
              {dtError.message}
            </div>
          )}

          <CardContent className="pt-6">
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
              <div className="lg:col-span-7">
                <LogWorkoutForm
                  dtLog={dtLog}
                  updateDtLog={updateDtLog}
                  todaySession={todaySession}
                  benchmarkKey={benchmarkKey}
                  benchmarkValue={benchmarkValue}
                  onBenchmarkKeyChange={setBenchmarkKey}
                  onBenchmarkValueChange={setBenchmarkValue}
                  signedIn={signedIn}
                  token={token}
                  dtLoading={dtLoading}
                  dtDose={dtDose}
                  onSubmit={handleDtLog}
                  onSimulate={handleDtSimulate}
                  onCrash={handleDtCrash}
                />
              </div>

              <div className="lg:col-span-5 space-y-6">
                <NextSessionCard token={token} dtRxLoading={dtRxLoading} dtRx={dtRx} todaySession={todaySession} />
                <StateSnapshot dtState={dtState} />
              </div>
            </div>
          </CardContent>
        </Card>

        <PatternPreviewDemo />
      </motion.div>
    </div>
  );
}

