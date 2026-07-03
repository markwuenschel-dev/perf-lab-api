// src/perflab/screens/SettingsScreen.tsx
import { useCallback, useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/auth/useAuth";
import * as api from "@/api/perfLabClient";
import type { ApiError, ProfileRead, ProfileUpdate } from "@/types";
import { TRAINING_GOALS, usePerfLab } from "../store";
import type { Settings } from "../store";
import { Card, SectionLabel } from "../ui";

const ACCENTS = ["#c6f135", "#45d6c4", "#86b8ff", "#f5c451", "#ff8a5c"];
const EXPERIENCE_LEVELS = ["beginner", "intermediate", "advanced"];

const inputCls = "mt-2 w-full rounded-[11px] border border-white/10 bg-panel px-[13px] py-[11px] text-[14px] text-ink";
const segCls = (active: boolean) =>
  cn(
    "flex-1 cursor-pointer rounded-[10px] border p-[11px] text-center text-[13px] font-semibold leading-none",
    active ? "border-ac/40 bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute",
  );

function Seg({ options, value, onChange, className }: { options: string[]; value: string; onChange: (v: string) => void; className?: string }) {
  return (
    <div className={cn("mt-2 flex gap-2", className)}>
      {options.map((o) => (
        <div key={o} onClick={() => onChange(o)} className={segCls(value === o)}>{o}</div>
      ))}
    </div>
  );
}

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <div onClick={onClick} className={cn("relative h-[22px] w-[38px] flex-none cursor-pointer rounded-full transition-colors", on ? "bg-ac" : "bg-white/[0.12]")}>
      <div className={cn("absolute top-[3px] h-[16px] w-[16px] rounded-full transition-all", on ? "left-[19px] bg-[#0a0c10]" : "left-[3px] bg-mute")} />
    </div>
  );
}

// ── Performance profile (backend-synced) ──────────────────────────────────
// The athlete profile is the *durable* store: register seeds an empty shell,
// onboarding fills it once, and this card lets the user read it back and edit it
// any time via GET/PATCH /v1/profile. The local `settings` (units, accent…) are
// per-device prefs and live in localStorage instead — see store.tsx.

type ProfileForm = {
  display_name: string;
  experience_level: string;
  experience_years: string;
  available_days_per_week: string;
  session_duration_minutes: string;
  bodyweight_kg: string;
  squat_1rm_kg: string;
  bench_1rm_kg: string;
  deadlift_1rm_kg: string;
  run_5k: string; // mm:ss
};

const EMPTY_FORM: ProfileForm = {
  display_name: "",
  experience_level: "beginner",
  experience_years: "",
  available_days_per_week: "",
  session_duration_minutes: "",
  bodyweight_kg: "",
  squat_1rm_kg: "",
  bench_1rm_kg: "",
  deadlift_1rm_kg: "",
  run_5k: "",
};

const numStr = (n: number | null | undefined): string => (n == null ? "" : String(n));
const numOrNull = (v: string): number | null => {
  const t = v.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
};
const secToMMSS = (s: number | null | undefined): string => {
  if (s == null) return "";
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
};
const mmssToSec = (v: string): number | null => {
  const t = v.trim();
  if (t === "") return null;
  if (t.includes(":")) {
    const [mm, ss] = t.split(":");
    const m = Number(mm);
    const s = Number(ss);
    if (Number.isFinite(m) && Number.isFinite(s)) return m * 60 + s;
    return null;
  }
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
};

function formFromProfile(p: ProfileRead): ProfileForm {
  return {
    display_name: p.display_name ?? "",
    experience_level: p.experience_level,
    experience_years: numStr(p.experience_years),
    available_days_per_week: numStr(p.available_days_per_week),
    session_duration_minutes: numStr(p.session_duration_minutes),
    bodyweight_kg: numStr(p.bodyweight_kg),
    squat_1rm_kg: numStr(p.squat_1rm_kg),
    bench_1rm_kg: numStr(p.bench_1rm_kg),
    deadlift_1rm_kg: numStr(p.deadlift_1rm_kg),
    run_5k: secToMMSS(p.run_5k_seconds),
  };
}

function patchFromForm(f: ProfileForm): ProfileUpdate {
  const patch: ProfileUpdate = {
    experience_level: f.experience_level,
    // Nullable fields: an empty input clears the stored value.
    display_name: f.display_name.trim() === "" ? null : f.display_name.trim(),
    bodyweight_kg: numOrNull(f.bodyweight_kg),
    squat_1rm_kg: numOrNull(f.squat_1rm_kg),
    bench_1rm_kg: numOrNull(f.bench_1rm_kg),
    deadlift_1rm_kg: numOrNull(f.deadlift_1rm_kg),
    run_5k_seconds: mmssToSec(f.run_5k),
  };
  // Required-ish fields: only send when the input parses, so a blank doesn't
  // null out a non-nullable column.
  const yrs = numOrNull(f.experience_years);
  if (yrs != null) patch.experience_years = yrs;
  const days = numOrNull(f.available_days_per_week);
  if (days != null) patch.available_days_per_week = Math.round(days);
  const dur = numOrNull(f.session_duration_minutes);
  if (dur != null) patch.session_duration_minutes = Math.round(dur);
  return patch;
}

type SaveState = "idle" | "saving" | "saved" | "error";

function PerformanceProfileCard() {
  const { token, isAuthenticated, refreshProfile } = useAuth();
  const [form, setForm] = useState<ProfileForm>(EMPTY_FORM);
  const [loading, setLoading] = useState(false);
  const [save, setSave] = useState<SaveState>("idle");
  const [error, setError] = useState<string | null>(null);

  const field = useCallback(
    <K extends keyof ProfileForm>(key: K) =>
      (v: string) => {
        setForm((f) => ({ ...f, [key]: v }));
        setSave("idle");
      },
    [],
  );

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const p = await api.getProfile(token);
        if (!cancelled) setForm(formFromProfile(p));
      } catch (e) {
        if (!cancelled) {
          const msg = (e as ApiError)?.message;
          setError(typeof msg === "string" ? msg : "Couldn't load your profile.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function onSave() {
    if (!token) return;
    setSave("saving");
    setError(null);
    try {
      const saved = await api.updateProfile(patchFromForm(form), token);
      setForm(formFromProfile(saved)); // re-seed from the persisted row
      // Push the fresh profile into AuthContext so the sidebar name/initials
      // update live without a reload.
      void refreshProfile();
      setSave("saved");
    } catch (e) {
      const msg = (e as ApiError)?.message;
      setError(typeof msg === "string" ? msg : "Couldn't save — check your entries.");
      setSave("error");
    }
  }

  if (!isAuthenticated) {
    return (
      <Card className="p-[22px]">
        <SectionLabel className="mb-3">Performance profile</SectionLabel>
        <div className="text-[12.5px] font-medium leading-[1.5] text-[#7c818c]">
          Sign in to load and edit your athlete profile (experience, lifts, biometrics).
          Guest sessions aren't saved.
        </div>
      </Card>
    );
  }

  const numInput = (key: keyof ProfileForm, label: string, placeholder: string, mono = true) => (
    <label className="block">
      <span className="text-[12px] font-medium leading-none text-mute">{label}</span>
      <input
        value={form[key]}
        onChange={(e) => field(key)(e.target.value)}
        inputMode="decimal"
        placeholder={placeholder}
        className={cn(inputCls, mono && "font-mono")}
      />
    </label>
  );

  return (
    <Card className="p-[22px]">
      <div className="mb-4 flex items-center justify-between">
        <SectionLabel>Performance profile</SectionLabel>
        {loading && <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-faint">loading…</span>}
      </div>

      <div className="flex flex-col gap-4">
        <label className="block max-w-[320px]">
          <span className="text-[12px] font-medium leading-none text-mute">Name</span>
          <input
            value={form.display_name}
            onChange={(e) => field("display_name")(e.target.value)}
            placeholder="e.g. Mark Wuenschel"
            className={inputCls}
          />
        </label>
        <div>
          <span className="text-[12px] font-medium leading-none text-mute">Experience level</span>
          <Seg options={EXPERIENCE_LEVELS} value={form.experience_level} onChange={field("experience_level")} />
        </div>
        <div className="grid grid-cols-2 gap-4">
          {numInput("experience_years", "Experience (years)", "e.g. 3")}
          {numInput("bodyweight_kg", "Bodyweight (kg)", "e.g. 74.5")}
          {numInput("available_days_per_week", "Training days / week", "1–7")}
          {numInput("session_duration_minutes", "Session length (min)", "e.g. 60")}
        </div>

        <div className="mt-1 grid grid-cols-3 gap-4">
          {numInput("squat_1rm_kg", "Squat 1RM (kg)", "—")}
          {numInput("bench_1rm_kg", "Bench 1RM (kg)", "—")}
          {numInput("deadlift_1rm_kg", "Deadlift 1RM (kg)", "—")}
        </div>

        <label className="block max-w-[220px]">
          <span className="text-[12px] font-medium leading-none text-mute">5K time (mm:ss)</span>
          <input
            value={form.run_5k}
            onChange={(e) => field("run_5k")(e.target.value)}
            placeholder="e.g. 22:30"
            className={`${inputCls} font-mono`}
          />
        </label>
      </div>

      {error && (
        <div className="mt-4 flex items-start gap-[9px] rounded-[11px] border border-hot/[0.3] bg-hot/[0.05] px-3 py-[10px]">
          <span className="text-[13px] leading-none text-hot">!</span>
          <span className="text-[11.5px] font-medium leading-[1.45] text-[#cf9a93]">{error}</span>
        </div>
      )}

      <div className="mt-5 flex items-center gap-3">
        <button
          type="button"
          onClick={() => void onSave()}
          disabled={save === "saving" || loading}
          className="rounded-[11px] bg-gradient-to-r from-ac to-[#a7e36e] px-5 py-[12px] text-[13px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60"
        >
          {save === "saving" ? "Saving…" : "Save profile"}
        </button>
        {save === "saved" && <span className="text-[12px] font-medium text-ac">Saved ✓</span>}
      </div>
    </Card>
  );
}

export function SettingsScreen() {
  const { state, actions } = usePerfLab();
  const auth = useAuth();
  const s = state.settings;
  const notif: [keyof Settings, string, string][] = [
    ["notifReadiness", "Readiness alerts", "When readiness crashes below 40."],
    ["notifTissue", "Tissue-load warnings", "When a region exceeds 60."],
    ["notifWeekly", "Weekly summary", "A Monday digest of the week ahead."],
  ];

  return (
    <section className="flex max-w-[780px] flex-col gap-4 px-[30px] pb-9 pt-[26px]">
      <header>
        <h1 className="m-0 text-[25px] font-bold leading-none tracking-[-0.02em] text-ink">Settings</h1>
        <p className="m-0 mt-[9px] text-[13.5px] font-medium leading-[1.5] text-[#7c818c]">Profile, units and preferences — editable any time.</p>
      </header>

      <PerformanceProfileCard />

      <Card className="p-[22px]">
        <SectionLabel className="mb-4">Profile</SectionLabel>
        <label className="block max-w-[280px]">
          <span className="text-[12px] font-medium leading-none text-mute">Sex</span>
          <Seg options={["Female", "Male"]} value={s.sex} onChange={(v) => actions.setSetting("sex", v)} />
        </label>
        <div className="mt-[14px] flex items-center gap-[9px] rounded-[11px] border border-info/[0.18] bg-info/[0.06] px-3 py-[10px]">
          <span className="text-[13px] text-info">ⓘ</span><span className="text-[11.5px] font-medium leading-[1.45] text-mute">VO₂ doesn't use age &amp; sex in v0.3 — stored on this device for upcoming model versions.</span>
        </div>
      </Card>

      <Card className="p-[22px]">
        <SectionLabel className="mb-4">Preferences</SectionLabel>
        <div className="flex flex-col gap-4">
          <label className="block">
            <span className="text-[12px] font-medium leading-none text-mute">Training goal</span>
            <select
              value={s.goal}
              onChange={(e) => actions.setSetting("goal", e.target.value)}
              className={inputCls}
              style={{ colorScheme: "dark" }}
            >
              {TRAINING_GOALS.map((g) => (
                <option key={g.value} value={g.value}>{g.label}</option>
              ))}
            </select>
            <span className="mt-2 block text-[11px] font-medium leading-[1.4] text-faint">Drives your prescribed sessions — anything from strength to marathon.</span>
          </label>
          <div>
            <span className="text-[12px] font-medium leading-none text-mute">Units</span>
            <Seg options={["Metric (km)", "Imperial (mi)"]} value={s.units} onChange={(v) => actions.setSetting("units", v)} />
          </div>
        </div>
      </Card>

      <Card className="p-[22px]">
        <SectionLabel className="mb-[6px]">Accent</SectionLabel>
        <div className="mb-[14px] text-[12px] font-medium leading-[1.5] text-[#7c818c]">Highlight colour for charts and active states.</div>
        <div className="flex items-center gap-4">
          {ACCENTS.map((c) => (
            <div
              key={c}
              onClick={() => actions.setSetting("accent", c)}
              className="h-[30px] w-[30px] cursor-pointer rounded-full"
              style={{ background: c, boxShadow: `0 0 0 2px #111419, 0 0 0 ${s.accent === c ? `4px ${c}` : "2px transparent"}` }}
            />
          ))}
        </div>
      </Card>

      <Card className="p-[22px]">
        <SectionLabel className="mb-4">Notifications</SectionLabel>
        <div className="flex flex-col gap-1">
          {notif.map(([key, title, desc], i) => (
            <div key={key} className={cn("flex items-center justify-between py-[11px]", i < notif.length - 1 && "border-b border-white/[0.05]")}>
              <div>
                <div className="text-[13px] font-semibold leading-none text-[#e6e8ec]">{title}</div>
                <div className="mt-1 text-[11px] font-medium leading-[1.4] text-faint">{desc}</div>
              </div>
              <Toggle on={s[key] as boolean} onClick={() => actions.setSetting(key, !s[key])} />
            </div>
          ))}
        </div>
      </Card>

      <Card className="flex items-center justify-between p-[22px]">
        <div>
          <div className="text-[13px] font-semibold leading-none text-[#e6e8ec]">Account</div>
          <div className="mt-[5px] font-mono text-[11px] leading-none text-faint">
            {auth.isAuthenticated
              ? auth.user?.email ?? auth.email
              : auth.isGuest
                ? "Guest session — nothing is saved. Sign in to keep your data."
                : "Not connected — sign in to sync the live twin."}
          </div>
        </div>
        {auth.isAuthenticated ? (
          <button
            onClick={auth.logout}
            className="rounded-[9px] border border-hot/25 bg-hot/[0.08] px-4 py-[10px] text-[12.5px] font-semibold leading-none text-hot"
          >
            Sign out
          </button>
        ) : (
          <button
            onClick={actions.openAuth}
            className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-4 py-[10px] text-[12.5px] font-semibold leading-none text-[#0a0c10]"
          >
            {auth.isGuest ? "Sign in to save →" : "Connect account →"}
          </button>
        )}
      </Card>

      <div className="px-1 font-mono text-[11px] leading-none text-faint">perf-lab-web · S(t) v0.3</div>
    </section>
  );
}
