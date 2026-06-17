// src/components/twin/TwinConsoleHeader.tsx
import { motion } from "framer-motion";
import { TRAINING_GOALS, type TrainingGoalValue } from "../../trainingGoals";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

type TwinConsoleHeaderProps = {
  dtGoal: TrainingGoalValue;
  onGoalChange: (goal: TrainingGoalValue) => void;
  onRefreshRx: () => void;
  token: string | null;
};

export function TwinConsoleHeader({
  dtGoal,
  onGoalChange,
  onRefreshRx,
  token,
}: TwinConsoleHeaderProps) {
  return (
    <div className="relative">
      {/* Top accent bar */}
      <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-neon-cyan via-neon-magenta to-neon-violet" />

      <div className="flex flex-col gap-4 px-6 pt-6 pb-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-mono tracking-[1px] text-neon-cyan">DIGITAL TWIN • CONTROL LOOP</p>
          <h2 className="mt-1 text-2xl font-semibold tracking-tight text-white">
            S(t), D(t), and your next session
          </h2>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-zinc-200">GOAL</span>
            <Select value={dtGoal} onValueChange={(v) => onGoalChange(v as TrainingGoalValue)}>
              <SelectTrigger className="w-40 bg-black/60 border-white/20 text-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRAINING_GOALS.map(({ value, label }) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <motion.button
            whileTap={{ scale: 0.95 }}
            onClick={onRefreshRx}
            disabled={!token}
            className="px-5 py-2.5 rounded-2xl border border-white/20 bg-white/5 text-sm font-medium text-white hover:border-neon-cyan/50 disabled:opacity-40 transition-colors"
          >
            Refresh u(t)
          </motion.button>
        </div>
      </div>
    </div>
  );
}