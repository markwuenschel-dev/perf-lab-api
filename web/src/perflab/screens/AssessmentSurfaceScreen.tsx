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
  submitStrengthEvidence,
} from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import { isImperial, unitLabel } from "@/lib/units";
import type { AssessmentBenchmarkCard, ApiError, OnboardingStateResponse } from "@/types";
import { Card, Pill, ScreenHeader, SectionLabel } from "../ui";
import { InfoTip, type InfoSection } from "../InfoTip";
import { axisLabel, BAND } from "../prescription/axes";
import { MeasurementRecommendations } from "../prescription/MeasurementRecommendations";
import { ResourceState } from "../ResourceState";
import { assertNever, resourceData } from "../resource";
import { usePerfLab } from "../store";
import { useAuthedResource } from "../useAuthedResource";
import { StrengthEvidenceFields } from "./StrengthEvidenceFields";
import {
  EMPTY_STRENGTH_FORM,
  strengthEvidenceBody,
  type StrengthForm,
  type WeightUnit,
} from "./strengthEvidenceBody";

type Mode = "onramp" | "retest";

const inputCls =
  "w-full rounded-[10px] border border-white/10 bg-panel px-3 py-2 text-[14px] text-ink font-mono";

// The band vocabulary moved to prescription/axes so this screen and the prescription
// explanation cannot drift into two sets of words for the same three literals. Aliased
// rather than renamed at the call site to keep this diff about the move.
const CONF = BAND;

// What the certainty band on a card means. The bands come from live per-axis variance only
// (app/logic/confidence_presentation.py): a benchmark shrinks it, an experience-based starting
// estimate leaves it provisional, and an unseeded axis is insufficient.
const CONFIDENCE_HELP: InfoSection[] = [
  { text: "How certain the twin currently is about the axes this benchmark measures." },
  { heading: BAND.established.label, text: "Low uncertainty, as after a benchmark has measured it." },
  { heading: BAND.provisional.label, text: "Still uncertain, as with an experience-based starting estimate." },
  { heading: BAND.insufficient.label, text: "Very uncertain: there is not enough evidence yet." },
];

/** A card's help: what it measures and how to measure it, kept as separate sections. */
function benchmarkHelp(card: AssessmentBenchmarkCard, unit: string): InfoSection[] {
  return [
    { heading: "What it measures", text: card.description ?? "No description yet." },
    { heading: "How to measure", text: card.protocol_summary ?? "No measurement instructions yet." },
    { heading: "Unit", text: unitLabel(unit) },
    ...(card.measures_axes.length > 0
      ? [{ heading: "Axes it measures", text: card.measures_axes.map(axisLabel).join(", ") }]
      : []),
  ];
}

export function AssessmentSurfaceScreen() {
  const { token } = useAuth();
  const { state, actions } = usePerfLab();
  // Strength reports are typed in the athlete's selected unit; the request carries kilograms.
  const unit: WeightUnit = isImperial(state.settings.units) ? "lb" : "kg";
  const [mode, setMode] = useState<Mode>("onramp");
  const [refreshKey, setRefreshKey] = useState(0);
  const surface = useAuthedResource(
    (t) => getAssessmentSurface(t, mode),
    [mode, refreshKey],
  );

  // The mode row sits ABOVE the state-dependent body and shows the focus chip
  // whenever a payload is on screen, so it reads the union directly rather than
  // through the branch. `resourceData` narrows to a real payload or null — it
  // never invents a fallback catalog.
  const loaded = resourceData(surface);

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeader
        title="Assess"
        badge={<Pill>/benchmarks/assessment-surface</Pill>}
        subtitle="One measurement layer for every domain. Log a benchmark and the twin updates itself — nothing here is a gate; unmeasured axes are surfaced as measurement debt."
      />

      <OnboardingBanner />

      {/* The measurement ask arrives on the PRESCRIPTION but belongs here, where the
          athlete can act on it. Renders nothing when there is nothing to ask for.
          `loaded` lets each row name the benchmark that actually measures that axis. */}
      <MeasurementRecommendations surface={loaded} />

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
        {loaded && loaded.active_domains.length > 0 && (
          <span className="ml-2 text-[11.5px] font-medium leading-none text-dim">
            focused on {loaded.active_domains.join(", ")}
          </span>
        )}
      </div>

      {/*
        The body is placeholder-box shaped in every non-success state, which is
        exactly the `box` variant of the shared boundary — the local
        PlaceholderBox was a duplicate of it and is gone. The screen no longer
        picks the branch, so the two states the old `!data` test collapsed are
        now distinct: a signed-out visitor is told to sign in instead of being
        told their own catalog is empty.
      */}
      <ResourceState
        resource={surface}
        isEmpty={(s) => s.groups.length === 0}
        guest={{
          body: "Sign in to see which benchmarks are worth assessing next — the catalog is filtered by your domains and ranked by measurement debt.",
          action: { label: "Sign in →", onClick: actions.openAuth },
        }}
        loadingContent={{ body: "Loading your assessment surface…" }}
        empty={{
          body: "No benchmarks match your domains yet. Add an objective or goal to focus the catalog, or switch to Retest.",
        }}
        staleLabel="Couldn't refresh your assessment surface — showing your last loaded benchmarks."
      >
        {(data) => {
          const recommended = new Set(data.recommended);
          return (
            <div className="flex flex-col gap-[22px]">
              {data.groups.map((group) => (
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
                        unit={unit}
                        onSubmitted={() => setRefreshKey((k) => k + 1)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          );
        }}
      </ResourceState>
    </section>
  );
}

// The banner reports on onboarding state, so it has nothing honest to say until
// one has actually loaded: no token, no first payload yet, and a failed load all
// render nothing — as before, but now as four named states rather than one
// collapsed `data === null`. There is no notice to show in those states, so this
// surface is not card-shaped and takes the exhaustive-switch consumer instead of
// <ResourceState>. A refresh — including a failed one — keeps the banner on
// screen, because the resource stays `success`.
function OnboardingBanner() {
  const { token } = useAuth();
  const [refreshKey, setRefreshKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const state = useAuthedResource((t) => getOnboardingState(t), [refreshKey]);

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

  switch (state.status) {
    case "guest":
    case "loading":
    case "error":
      return null;
    case "success":
      return <OnboardingBannerCard s={state.data} busy={busy} onLeave={leave} />;
    default:
      return assertNever(state);
  }
}

function OnboardingBannerCard({
  s,
  busy,
  onLeave,
}: {
  s: OnboardingStateResponse;
  busy: boolean;
  onLeave: () => void;
}) {
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
            onClick={onLeave}
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
  unit,
  onSubmitted,
}: {
  card: AssessmentBenchmarkCard;
  mode: Mode;
  recommended: boolean;
  token: string | null;
  /** The unit a strength report is typed in. */
  unit: WeightUnit;
  onSubmitted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [strength, setStrength] = useState<StrengthForm>(EMPTY_STRENGTH_FORM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const conf = card.confidence_status ? CONF[card.confidence_status] : null;
  // Canonical-lift e1RM cards report characterized evidence (S2), never a bare number.
  const isStrength = card.strength_evidence_entry === true;
  const shownUnit = isStrength ? unit : card.unit;

  async function submitStrength() {
    if (!token) return;
    const built = strengthEvidenceBody(card.code, mode, strength, { unit });
    if (!built.ok) {
      setError(built.error);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await submitStrengthEvidence(built.body, token);
      setOpen(false);
      setStrength(EMPTY_STRENGTH_FORM);
      onSubmitted();
    } catch (e) {
      setError((e as ApiError)?.message ?? "Couldn't save that result.");
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    if (isStrength) return submitStrength();
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
            <InfoTip label={`About ${card.name}`} sections={benchmarkHelp(card, shownUnit)} />
            {recommended && (
              <span className="flex-none rounded-full border border-ac/30 bg-ac/[0.1] px-2 py-[3px] text-[9.5px] font-semibold uppercase leading-none tracking-[0.08em] text-ac">
                recommended
              </span>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-[6px] text-[11px] font-medium leading-none text-dim">
            <span className="font-mono">{unitLabel(shownUnit)}</span>
            {card.measures_axes.length > 0 && (
              <span>· measures {card.measures_axes.map(axisLabel).join(", ")}</span>
            )}
          </div>
        </div>
        {conf && (
          <span className="flex flex-none items-center gap-1">
            <span className={`rounded-full border px-2 py-1 text-[10px] font-semibold leading-none ${conf.cls}`}>
              {conf.label}
            </span>
            <InfoTip label="About this confidence label" sections={CONFIDENCE_HELP} />
          </span>
        )}
      </div>

      {open ? (
        <div className="mt-3 flex flex-col gap-2">
          {isStrength ? (
            <StrengthEvidenceFields form={strength} onChange={setStrength} unit={unit} />
          ) : (
            <input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              inputMode="decimal"
              placeholder={`Result in ${unitLabel(card.unit)}`}
              className={inputCls}
              autoFocus
            />
          )}
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

