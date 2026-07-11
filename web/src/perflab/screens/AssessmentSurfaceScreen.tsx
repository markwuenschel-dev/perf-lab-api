// src/perflab/screens/AssessmentSurfaceScreen.tsx
//
// The one benchmark assessment surface (P10, ADR-0047): the domain-filtered catalog
// with a measurement-debt ranking of what to assess next. Every submit is a single
// benchmark_observation — the backend owns the state seed/update (ADR-0058); the
// frontend never seeds capacity. Replaces the retired standalone running Field Test.
import { useState } from "react";
import {
  completeOnboarding,
  getAssessmentSurface,
  getOnboardingState,
  submitBenchmarkObservation,
} from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import type { AssessmentBenchmarkCard, ApiError } from "@/types";
import { Card, Pill, ScreenHeader, SectionLabel } from "../ui";
import { useAuthedResource } from "../useAuthedResource";

type Mode = "onramp" | "retest";

const inputCls =
  "w-full rounded-[10px] border border-white/10 bg-panel px-3 py-2 text-[14px] text-ink font-mono";

const CONF: Record<string, { label: string; cls: string }> = {
  established: { label: "measured", cls: "text-mint border-mint/30 bg-mint/[0.06]" },
  provisional: { label: "provisional", cls: "text-warn border-warn/30 bg-warn/[0.06]" },
  insufficient: { label: "unmeasured", cls: "text-hot border-hot/30 bg-hot/[0.06]" },
};

export function AssessmentSurfaceScreen() {
  const { token } = useAuth();
  const [mode, setMode] = useState<Mode>("onramp");
  const [refreshKey, setRefreshKey] = useState(0);
  const surface = useAuthedResource(
    (t) => getAssessmentSurface(t, mode),
    [mode, refreshKey],
  );

  const recommended = new Set(surface.data?.recommended ?? []);

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader
        title="Assess"
        badge={<Pill>/benchmarks/assessment-surface</Pill>}
        subtitle="One measurement layer for every domain. Log a benchmark and the twin updates itself — nothing here is a gate; unmeasured axes are surfaced as measurement debt."
      />

      <OnboardingBanner />

      <div className="flex items-center gap-2">
        {(["onramp", "retest"] as Mode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`rounded-[9px] border px-4 py-2 text-[12px] font-semibold leading-none ${
              mode === m
                ? "border-ac/40 bg-ac/[0.1] text-ac"
                : "border-white/10 bg-white/[0.03] text-mute"
            }`}
          >
            {m === "onramp" ? "Onramp" : "Retest"}
          </button>
        ))}
        {surface.data && surface.data.active_domains.length > 0 && (
          <span className="ml-2 text-[11.5px] font-medium leading-none text-dim">
            focused on {surface.data.active_domains.join(", ")}
          </span>
        )}
      </div>

      {surface.loading ? (
        <PlaceholderBox>Loading your assessment surface…</PlaceholderBox>
      ) : surface.error ? (
        <PlaceholderBox tone="error">{surface.error}</PlaceholderBox>
      ) : !surface.data || surface.data.groups.length === 0 ? (
        <PlaceholderBox>
          No benchmarks match your domains yet. Add an objective or goal to focus the
          catalog, or switch to Retest.
        </PlaceholderBox>
      ) : (
        <div className="flex flex-col gap-[22px]">
          {surface.data.groups.map((group) => (
            <div key={group.domain} className="flex flex-col gap-3">
              <SectionLabel className="capitalize">{group.domain}</SectionLabel>
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {group.cards.map((card) => (
                  <BenchmarkCard
                    key={card.code}
                    card={card}
                    mode={mode}
                    recommended={recommended.has(card.code)}
                    token={token}
                    onSubmitted={() => setRefreshKey((k) => k + 1)}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function OnboardingBanner() {
  const { token } = useAuth();
  const [refreshKey, setRefreshKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const state = useAuthedResource((t) => getOnboardingState(t), [refreshKey]);
  const s = state.data;
  if (!s) return null;

  async function leave() {
    if (!token) return;
    setBusy(true);
    try {
      await completeOnboarding(token, "done_for_now");
      setRefreshKey((k) => k + 1);
    } finally {
      setBusy(false);
    }
  }

  const twin = s.twin;
  const done = s.status === "completed";

  return (
    <Card className="flex flex-col gap-3 p-[18px]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-semibold leading-none text-ink">Your twin</span>
          <span className={`rounded-full border px-2 py-1 text-[10px] font-semibold leading-none ${
            twin.provisional ? "text-warn border-warn/30 bg-warn/[0.06]" : "text-mint border-mint/30 bg-mint/[0.06]"
          }`}>
            {twin.seeded ? (twin.provisional ? "provisional" : "established") : "not seeded"}
          </span>
          {twin.overall_confidence && (
            <span className="text-[11.5px] font-medium leading-none text-dim">
              overall confidence: {twin.overall_confidence}
            </span>
          )}
        </div>
        {!done && (
          <button
            onClick={leave}
            disabled={busy}
            className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-2 text-[12px] font-semibold leading-none text-soft disabled:opacity-60"
          >
            {busy ? "…" : "I’m done for now"}
          </button>
        )}
      </div>
      {!s.can_prescribe && s.missing_basics.length > 0 && (
        <div className="rounded-[10px] border border-info/[0.18] bg-info/[0.06] px-3 py-[10px] text-[11.5px] font-medium leading-[1.5] text-mute">
          To prescribe safely, finish the basics in Onboarding: {s.missing_basics.join(", ")}. Assessing a
          benchmark is never required — it just sharpens the estimate.
        </div>
      )}
    </Card>
  );
}

function BenchmarkCard({
  card,
  mode,
  recommended,
  token,
  onSubmitted,
}: {
  card: AssessmentBenchmarkCard;
  mode: Mode;
  recommended: boolean;
  token: string | null;
  onSubmitted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const conf = card.confidence_status ? CONF[card.confidence_status] : null;

  async function submit() {
    if (!token) return;
    const raw = Number(value);
    if (!Number.isFinite(raw)) {
      setError("Enter a numeric result.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await submitBenchmarkObservation(
        {
          benchmark_code: card.code,
          raw_value: raw,
          source: "manual",
          validity_status: "valid",
          collection_mode: mode === "onramp" ? "onboarding_onramp" : "retest",
        },
        token,
      );
      setOpen(false);
      setValue("");
      onSubmitted();
    } catch (e) {
      setError((e as ApiError)?.message ?? "Couldn't save that result.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="p-[18px]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-[14px] font-semibold leading-none text-ink">{card.name}</span>
            {recommended && (
              <span className="flex-none rounded-full border border-ac/30 bg-ac/[0.1] px-2 py-[3px] text-[9.5px] font-semibold uppercase leading-none tracking-[0.08em] text-ac">
                recommended
              </span>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-[6px] text-[11px] font-medium leading-none text-dim">
            <span className="font-mono">{card.unit}</span>
            {card.measures_axes.length > 0 && (
              <span>· measures {card.measures_axes.join(", ")}</span>
            )}
          </div>
        </div>
        {conf && (
          <span className={`flex-none rounded-full border px-2 py-1 text-[10px] font-semibold leading-none ${conf.cls}`}>
            {conf.label}
          </span>
        )}
      </div>

      {card.protocol_summary && (
        <div className="mt-3 text-[11.5px] font-medium leading-[1.5] text-mute">{card.protocol_summary}</div>
      )}

      {open ? (
        <div className="mt-3 flex flex-col gap-2">
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            inputMode="decimal"
            placeholder={`Result in ${card.unit}`}
            className={inputCls}
            autoFocus
          />
          {error && <div className="text-[11px] font-medium leading-none text-hot">{error}</div>}
          <div className="flex gap-2">
            <button
              onClick={submit}
              disabled={busy}
              className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-4 py-2 text-[12px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60"
            >
              {busy ? "Saving…" : "Save result"}
            </button>
            <button
              onClick={() => { setOpen(false); setError(null); }}
              className="rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-2 text-[12px] font-semibold leading-none text-soft"
            >
              Do this later
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setOpen(true)}
          className="mt-3 rounded-[9px] border border-white/10 bg-white/[0.04] px-4 py-2 text-[12px] font-semibold leading-none text-soft"
        >
          Log result
        </button>
      )}
    </Card>
  );
}

function PlaceholderBox({
  children,
  tone = "empty",
}: {
  children: React.ReactNode;
  tone?: "empty" | "error";
}) {
  const cls =
    tone === "error"
      ? "border-hot/[0.3] bg-hot/[0.05] text-mute"
      : "border-dashed border-white/10 text-mute";
  return (
    <div className={`flex min-h-[240px] flex-col items-center justify-center gap-3 rounded-[18px] border p-[30px] text-center ${cls}`}>
      <div className="max-w-[360px] text-[12.5px] font-medium leading-[1.5]">{children}</div>
    </div>
  );
}
