// src/perflab/screens/LoginScreen.tsx
// Full-screen auth gate. Until a user is authenticated, App renders this instead
// of the app shell — login is a hard gate again (it was on-demand via a modal
// after the "Performance OS" redesign). Registering signs the user in and flips
// onboardingPending, which App routes into the onboarding takeover. A 401 on any
// /v1 call clears the token (sessionBridge → AuthProvider), which lands the user
// back here. The brand panel mirrors OnboardingScreen so gate → onboarding reads
// as one flow.
import { useState } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/auth/useAuth";
import type { ApiError } from "@/types";

type Mode = "login" | "register";

const labelCls =
  "font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.1em] text-[#9aa0ab]";
const inputCls =
  "mt-[9px] w-full rounded-[11px] border border-white/10 bg-panel px-[14px] py-3 text-[15px] text-ink";

const HIGHLIGHTS = [
  ["01", "Field test", "300 m + 1.5 mi → VO₂, profile & pace zones."],
  ["02", "Digital twin", "A state vector S(t) that evolves as you train."],
  ["03", "Adaptive plan", "Sessions prescribed against your readiness."],
] as const;

export function LoginScreen() {
  const { login, register, enterGuest, isLoading } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const isRegister = mode === "register";
  const canSubmit = email.trim() !== "" && password !== "" && !isLoading;

  async function submit() {
    if (!canSubmit) return;
    setError(null);
    try {
      // On success the token flips isAuthenticated → App swaps this screen out
      // for the shell (or, after register, the onboarding takeover).
      if (isRegister) await register(email.trim(), password);
      else await login(email.trim(), password);
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
    <div className="grid min-h-screen grid-cols-1 md:grid-cols-[minmax(0,440px)_1fr]">
      {/* brand panel — mirrors OnboardingScreen for visual continuity */}
      <div
        className="flex flex-col justify-between border-r border-white/[0.06] px-11 py-12"
        style={{ background: "radial-gradient(120% 80% at 0% 0%,#11321f,#0b0e13 55%)" }}
      >
        <div className="flex items-center gap-3">
          <div className="grid h-[34px] w-[34px] place-items-center rounded-[10px] bg-gradient-to-br from-ac to-teal text-[17px] font-extrabold leading-none text-[#0a0c10]">◆</div>
          <div>
            <div className="text-[16px] font-bold leading-none text-ink">PERF LAB</div>
            <div className="mt-[3px] font-mono text-[10px] leading-[1.3] tracking-[0.14em] text-faint">PERFORMANCE OS</div>
          </div>
        </div>
        <div>
          <h2 className="m-0 text-[34px] font-bold leading-[1.1] tracking-[-0.03em] text-ink">Sign in to your living model.</h2>
          <div className="mt-[30px] flex flex-col gap-[18px]">
            {HIGHLIGHTS.map(([n, t, d]) => (
              <div key={n} className="flex gap-[13px]">
                <span className="font-mono text-[14px] font-bold leading-[1.4] text-ac">{n}</span>
                <div>
                  <div className="text-[14px] font-semibold leading-none text-ink">{t}</div>
                  <div className="mt-[5px] text-[12.5px] font-medium leading-[1.5] text-[#7c818c]">{d}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="font-mono text-[11px] leading-none text-dim">v0.3 · perf-lab-web</div>
      </div>

      {/* form panel */}
      <div className="flex max-w-[620px] flex-col justify-center px-14 py-12">
        <div className="w-full max-w-[420px]">
          <div className="mb-2 flex items-center gap-[10px]">
            <h1 className="m-0 text-[30px] font-bold leading-[1.1] tracking-[-0.025em] text-ink">
              {isRegister ? "Create account" : "Sign in"}
            </h1>
          </div>
          <p className="m-0 mb-7 mt-3 text-[14px] font-medium leading-[1.5] text-[#7c818c]">
            {isRegister
              ? "Registering signs you in and seeds your athlete profile."
              : "Sign in to unlock the live twin, planning and workout logging."}
          </p>

          <div className="mb-[18px] grid grid-cols-2 gap-2">
            {(["login", "register"] as Mode[]).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => switchMode(m)}
                className={cn(
                  "rounded-[11px] border px-[13px] py-[11px] text-[13px] font-semibold leading-none",
                  m === mode
                    ? "border-ac/[0.45] bg-ac/[0.12] text-ac"
                    : "border-white/10 bg-panel text-mute",
                )}
              >
                {m === "login" ? "Sign in" : "Register"}
              </button>
            ))}
          </div>

          <div className="flex flex-col gap-[18px]">
            <label className="block">
              <span className={labelCls}>Email</span>
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                type="email"
                autoComplete="email"
                placeholder="you@example.com"
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className={labelCls}>Password</span>
              <input
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void submit();
                }}
                type="password"
                autoComplete={isRegister ? "new-password" : "current-password"}
                placeholder="••••••••"
                className={inputCls}
              />
            </label>

            {error && (
              <div className="flex items-start gap-[9px] rounded-[11px] border border-hot/[0.3] bg-hot/[0.05] px-3 py-[10px]">
                <span className="text-[13px] leading-none text-hot">!</span>
                <span className="text-[11.5px] font-medium leading-[1.45] text-[#cf9a93]">{error}</span>
              </div>
            )}

            <button
              type="button"
              onClick={() => void submit()}
              disabled={!canSubmit}
              className="rounded-[11px] bg-gradient-to-r from-ac to-[#a7e36e] p-[14px] text-[13.5px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60"
            >
              {isLoading
                ? isRegister
                  ? "Creating account…"
                  : "Signing in…"
                : isRegister
                  ? "Create account"
                  : "Sign in"}
            </button>

            {/* Guest / "try it" escape hatch — no account, nothing persisted. */}
            <div className="flex items-center gap-3">
              <span className="h-px flex-1 bg-white/[0.08]" />
              <span className="font-mono text-[10px] uppercase leading-none tracking-[0.14em] text-faint">or</span>
              <span className="h-px flex-1 bg-white/[0.08]" />
            </div>
            <button
              type="button"
              onClick={enterGuest}
              disabled={isLoading}
              className="rounded-[11px] border border-white/[0.12] bg-panel p-[13px] text-[13px] font-semibold leading-none text-soft disabled:opacity-60"
            >
              Skip — explore as guest
            </button>
            <p className="m-0 text-center text-[11px] font-medium leading-[1.5] text-faint">
              Guest mode onboards you into the live twin to play with — but nothing is saved.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
