// src/perflab/overlays/AuthModal.tsx
// Sign-in / register surface for the parked auth layer. Non-gating: the modal
// is opened on demand (Settings → account card) and, once authenticated, the
// JWT from useAuth() unblocks the /v1/* slices (Twin, Planning, Log Workout).
import { useState } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/auth/useAuth";
import type { ApiError } from "@/types";
import { usePerfLab } from "../store";
import { Pill } from "../ui";
import { CloseBtn } from "./LogWorkoutModal";

type Mode = "login" | "register";

const fieldCls =
  "mt-2 w-full rounded-[11px] border border-white/10 bg-panel px-[14px] py-3 text-[15px] text-ink";

export function AuthModal() {
  const { state, actions } = usePerfLab();
  const auth = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!state.authOpen) return null;

  const isRegister = mode === "register";
  const canSubmit = email.trim() !== "" && password !== "" && !auth.isLoading;

  async function submit() {
    if (!canSubmit) return;
    setError(null);
    try {
      if (isRegister) await auth.register(email.trim(), password);
      else await auth.login(email.trim(), password);
      setPassword("");
      actions.closeAuth();
    } catch (e) {
      setError(
        (e as ApiError)?.message ??
          "Authentication failed — check your details and that the backend is reachable.",
      );
    }
  }

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-8 backdrop-blur-[4px]"
      style={{ background: "rgba(4,5,8,.68)" }}
    >
      <div className="w-[420px] max-w-full overflow-hidden rounded-[18px] border border-white/[0.09] bg-surface shadow-[0_50px_110px_-30px_rgba(0,0,0,.75)]">
        <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-5">
          <div className="flex items-center gap-[10px]">
            <h2 className="m-0 text-[18px] font-bold leading-none tracking-[-0.01em] text-ink">
              {isRegister ? "Create account" : "Sign in"}
            </h2>
            <Pill>/auth</Pill>
          </div>
          <CloseBtn onClick={actions.closeAuth} />
        </div>

        <div className="flex flex-col gap-[18px] px-6 py-[22px]">
          <div className="grid grid-cols-2 gap-2">
            {(["login", "register"] as Mode[]).map((m) => (
              <button
                key={m}
                onClick={() => switchMode(m)}
                className={cn(
                  "rounded-[10px] border px-[13px] py-[10px] text-[12.5px] font-semibold leading-none",
                  m === mode
                    ? "border-ac/[0.45] bg-ac/[0.12] text-ac"
                    : "border-white/10 bg-panel text-mute",
                )}
              >
                {m === "login" ? "Sign in" : "Register"}
              </button>
            ))}
          </div>

          <label className="block">
            <span className="text-[12px] font-medium leading-none text-mute">Email</span>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              className={fieldCls}
            />
          </label>
          <label className="block">
            <span className="text-[12px] font-medium leading-none text-mute">Password</span>
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submit();
              }}
              type="password"
              autoComplete={isRegister ? "new-password" : "current-password"}
              placeholder="••••••••"
              className={fieldCls}
            />
          </label>

          {error && (
            <div className="flex items-start gap-[9px] rounded-[11px] border border-hot/[0.3] bg-hot/[0.05] px-3 py-[10px]">
              <span className="text-[13px] leading-none text-hot">!</span>
              <span className="text-[11.5px] font-medium leading-[1.45] text-[#cf9a93]">{error}</span>
            </div>
          )}

          <button
            onClick={() => void submit()}
            disabled={!canSubmit}
            className="rounded-[11px] bg-gradient-to-r from-ac to-[#a7e36e] p-[14px] text-[13.5px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60"
          >
            {auth.isLoading
              ? isRegister
                ? "Creating account…"
                : "Signing in…"
              : isRegister
                ? "Create account"
                : "Sign in"}
          </button>

          <p className="m-0 text-center text-[11.5px] font-medium leading-[1.5] text-[#7c818c]">
            {isRegister
              ? "Registering signs you in and seeds your athlete profile."
              : "Signing in unlocks the live twin, planning and workout logging."}
          </p>
        </div>
      </div>
    </div>
  );
}
