// src/perflab/screens/TwinScreen.tsx
//
// The Digital Twin screen. For an AUTHENTICATED athlete this is fully live: the
// state vector, its time-travel scrub, readiness, capacities, fatigue and tissue
// all come from GET /v1/state-history + GET /v1/readiness — no sim, no fabricated
// numbers. A GUEST sees a clearly-labelled preview driven by the deterministic
// sim (sim.ts), which is the ONLY place the VO2 / Profile / Skill mock tiles and
// the simulated Explain drawer appear.
//
// That guest preview lives in ./twin/GuestTwinPreview, not here: the boundary
// between sampled and live is a file boundary, so this file holds no fixture
// render tree at all. The two bodies still share the presentational pieces they
// genuinely have in common — ./twin/TissueBodyMap and ./twin/MiniTile — which
// take resolved values as props and make no claim about their provenance.
//
// Snapshot identity: cross-screen selection is a snapshot_id (store.selectedTwin
// SnapshotId), never a list index — the state-history window shifts as rows
// accrue. The slider/prev/next operate over the LOCAL index of the loaded window
// and write back the adjacent row's snapshot_id.
import * as api from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import type { ReadinessScore, StateHistorySnapshotRead, WorkoutPrescription } from "@/types";
import { usePerfLab } from "../store";
import { useAuthedResource } from "../useAuthedResource";
import { assertNever, resourceData, type AuthedResource } from "../resource";
import { Card, MetricBar, Pill, ReadinessRing, SectionLabel, SyncChip, WeakPointTags } from "../ui";
import { WhyThisSession } from "../prescription/WhyThisSession";
import { LoadExplanation } from "../prescription/LoadExplanation";
import { Chart, Line, Marker, useVizTheme } from "../viz";
import { meanFatigue, relativeTime } from "../stateVector";
import { CapacityView } from "./twin/CapacityView";
import { GuestTwinPreview } from "./twin/GuestTwinPreview";
import { MiniTile } from "./twin/MiniTile";
import { TissueBodyMap } from "./twin/TissueBodyMap";
import { viewingLabel } from "./twin/viewingLabel";
import { FATIGUE_ORDER, fatigueColor } from "../sim";
import { readinessColor, readinessNote, readinessWord } from "../readinessPresentation";

// meanFatigue + relativeTime come from the shared ../stateVector module, and
// viewingLabel from ./twin/viewingLabel — no longer copied module-local.
//
// Note which sim symbols survive here: only the palette/ordering helpers, which
// carry no fixture data. Every sample-data import (DAYS, CAP_CFG, SKILL_DEFS, …)
// left with GuestTwinPreview, so this file cannot render a fabricated number.

export function TwinScreen() {
  const { token } = useAuth();
  const { state, actions } = usePerfLab();

  const historyRes = useAuthedResource<StateHistorySnapshotRead[]>((t) => api.getStateHistory(t, 60), []);
  const readinessRes = useAuthedResource<ReadinessScore>((t) => api.getReadiness(t), [state.readinessRefreshKey]);

  const isGuest = token == null;
  const syncLabel = isGuest ? "Preview" : twinSyncLabel(historyRes);

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <ScreenHeaderTwin authed={!isGuest} syncLabel={syncLabel} onLog={actions.openLog} />

      <NextSessionCard />

      {isGuest ? (
        <GuestTwinPreview />
      ) : (
        <AuthedTwinBody historyRes={historyRes} readinessRes={readinessRes} />
      )}
    </section>
  );
}

/**
 * The header sync chip, read off the four availability states rather than a
 * loading flag. "Syncing…" covers a request in flight with nothing recorded to
 * show yet — including a refresh over a window that came back empty.
 */
function twinSyncLabel(resource: AuthedResource<StateHistorySnapshotRead[]>): string {
  switch (resource.status) {
    case "guest":
      return "Preview";
    case "loading":
      return "Syncing…";
    case "error":
      return "No recorded state";
    case "success": {
      const rows = resource.data;
      const newest = rows.length ? rows[rows.length - 1] : null;
      if (newest) return `Synced ${relativeTime(newest.timestamp)}`;
      return resource.refresh.status === "loading" ? "Syncing…" : "No recorded state";
    }
    default:
      return assertNever(resource);
  }
}

// ============================ AUTHENTICATED (LIVE) ============================

function AuthedTwinBody({
  historyRes,
  readinessRes,
}: {
  historyRes: AuthedResource<StateHistorySnapshotRead[]>;
  readinessRes: AuthedResource<ReadinessScore>;
}) {
  const { state, actions } = usePerfLab();
  const { accent } = useVizTheme();

  // The four-state ladder, now over the union: an exhaustive switch (this body
  // is a whole screen region, not a card, so <ResourceState> is the wrong
  // consumer). Order is the canonical one — guest → loading → error → empty.
  switch (historyRes.status) {
    // ---- GUEST: unreachable — TwinScreen routes guests to <GuestTwinPreview/>
    // before this body mounts. It is emphatically NOT the empty state below.
    case "guest":
      return null;
    // ---- LOADING: neutral skeletons, never sim ----
    case "loading":
      return <TwinSkeleton />;
    // ---- ERROR: honest retry, distinct from empty (no sim, no manufactured 50s) ----
    case "error":
      return (
        <Card className="px-[22px] py-8 text-center">
          <div className="text-[14px] font-semibold text-ink">Couldn&apos;t load your twin</div>
          <div className="mx-auto mt-2 max-w-[420px] text-[12.5px] font-medium leading-[1.5] text-mute">{historyRes.error.message}</div>
          <div className="mt-2 text-[12px] font-medium text-dim">Reload to try again.</div>
        </Card>
      );
    // A usable window is on screen; a failed refresh over it stays here rather
    // than collapsing to the error card.
    case "success":
      break;
    default:
      return assertNever(historyRes);
  }

  const rows = historyRes.data;

  // ---- EMPTY: real empty prompt (never default 50s) ----
  if (rows.length === 0) {
    return (
      <Card className="px-[22px] py-8 text-center">
        <div className="text-[14px] font-semibold text-ink">No twin state yet</div>
        <div className="mx-auto mt-2 max-w-[440px] text-[12.5px] font-medium leading-[1.5] text-mute">
          Log a workout or run a field test to seed your twin — your evolving state vector will appear here.
        </div>
        <button onClick={actions.openLog} className="mt-4 rounded-[9px] bg-ink px-[15px] py-[9px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">
          Log workout
        </button>
      </Card>
    );
  }

  // ---- THIN (len 1) / LIVE (len >= 2) ----
  const len = rows.length;
  const thin = len < 2;
  const showDelta = !thin;

  // Resolve the cross-screen snapshot_id to a LOCAL index in the loaded window.
  // If the requested id is not in the window, fall back EXPLICITLY to the newest
  // row — never silently reinterpret an old id as a position.
  const selId = state.selectedTwinSnapshotId;
  const found = selId != null ? rows.findIndex((r) => r.snapshot_id === selId) : -1;
  const di = found >= 0 ? found : len - 1;
  const row = rows[di];
  const startRow = rows[0];
  const isLatest = di === len - 1;

  const selectLocal = (li: number) => {
    const clamped = Math.max(0, Math.min(len - 1, li));
    actions.setSelectedTwinSnapshot(rows[clamped].snapshot_id);
  };

  const { date: vDate, when: vWhen } = viewingLabel(row.timestamp);
  const sparkData = rows.map((r, i) => [i, meanFatigue(r)] as [number, number]);

  // Canonical readiness for the LATEST snapshot only (PDR-0005: never recompute).
  const canonicalScore = resourceData(readinessRes)?.score ?? null;

  // ---- Fatigue / tissue: live decomposed axes (guarded for optional fields) ----
  const fRec = (row.fatigue_f ?? {}) as Record<string, number>;
  const tRec = (row.tissue_t ?? {}) as Record<string, number>;
  const fatigueOf = (label: string) => Math.round(fRec[label.toLowerCase()] ?? 0);
  const tissueOf = (label: string) => Math.round(tRec[label.toLowerCase()] ?? 0);

  // ---- Structural signal trend (guarded for di==0 / thin) ----
  const signalVal = row.s_struct_signal ?? 0;
  const prevRow = di > 0 ? rows[di - 1] : null;
  const signalTrend = prevRow ? (signalVal >= (prevRow.s_struct_signal ?? 0) ? "↗ rising" : "→ steady") : "—";
  const habitPct = Math.round((row.habit_strength ?? 0) * 100);

  return (
    <>
      {/* time-travel — axis is recorded snapshots, x = ordinal 0..len-1 */}
      <Card className="flex items-center gap-[22px] px-5 py-[15px]">
        <div className="min-w-[118px] flex-none">
          <SectionLabel className="text-faint">Viewing</SectionLabel>
          <div className="mt-[7px] flex items-baseline gap-2">
            <span className="text-[18px] font-bold leading-none text-ink">{vDate}</span>
            {vWhen && <span className="text-[11px] font-medium leading-none text-teal">{vWhen}</span>}
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <Chart
            width={560}
            height={40}
            padding={{ top: 5, right: 10, bottom: 5, left: 10 }}
            xDomain={[0, Math.max(1, len - 1)]}
            yDomain={[0, 100]}
            ariaLabel="Mean fatigue across recorded states"
            className="h-[38px] w-full"
          >
            {!thin && <Line data={sparkData} color={accent} opacity={0.7} label="Mean fatigue" />}
            <Marker x={di} y={meanFatigue(row)} color={accent} />
          </Chart>
          <input
            type="range"
            min={0}
            max={len - 1}
            value={di}
            disabled={thin}
            onChange={(e) => selectLocal(+e.target.value)}
            className="mt-[2px] w-full cursor-pointer disabled:cursor-default disabled:opacity-40"
            style={{ accentColor: "var(--ac)" }}
          />
          <div className="mt-[2px] font-mono text-[9px] leading-none text-dim">
            {thin ? "Only one recorded state so far" : "Mean fatigue · oldest → newest recorded state"}
          </div>
        </div>
        <div className="flex flex-none items-center gap-[7px]">
          <button onClick={() => selectLocal(di - 1)} disabled={thin || di === 0} className="h-[34px] w-[34px] rounded-[9px] border border-white/10 bg-white/[0.03] text-[15px] leading-none text-soft disabled:opacity-30">‹</button>
          <button onClick={() => selectLocal(di + 1)} disabled={thin || di === len - 1} className="h-[34px] w-[34px] rounded-[9px] border border-white/10 bg-white/[0.03] text-[15px] leading-none text-soft disabled:opacity-30">›</button>
          <button onClick={() => actions.setSelectedTwinSnapshot(rows[len - 1].snapshot_id)} className="rounded-[9px] bg-ink px-[13px] py-[9px] text-[12px] font-semibold leading-none text-[#0a0c10]">Today</button>
        </div>
      </Card>

      {/* readiness + two live tiles */}
      <div className="grid grid-cols-1 gap-[14px] lg:grid-cols-[300px_1fr]">
        <ReadinessCard isLatest={isLatest} canonicalScore={canonicalScore} readinessRes={readinessRes} meanF={Math.round(meanFatigue(row))} />
        <div className="grid grid-cols-2 gap-[14px]">
          <MiniTile
            tip="Consistency of training vs plan — habit strength on a 0–100 scale."
            label="Habit"
            value={<>{habitPct}<span className="text-[16px] text-faint">%</span></>}
            sub="adherence"
            bar={habitPct}
          />
          <MiniTile
            tip="Structural adaptation drive — how strongly recent load is stimulating tissue remodelling. Higher = actively building structure."
            label="Struct. signal"
            value={signalVal.toFixed(1)}
            sub="adaptation drive"
            foot={signalTrend}
            footColor="text-teal"
          />
        </div>
      </div>

      {/* capacities + confidence (extracted, BA-4) */}
      <CapacityView row={row} startRow={startRow} showDelta={showDelta} />

      {/* fatigue + tissue */}
      <div className="grid grid-cols-1 gap-[14px] lg:grid-cols-2">
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <SectionLabel>Fatigue · F(t)</SectionLabel>
            <div className="font-mono text-[10px] leading-none text-dim">0 fresh → 100 maxed</div>
          </div>
          <div className="flex flex-col gap-[13px]">
            {FATIGUE_ORDER.map((k) => {
              const v = fatigueOf(k);
              return <MetricBar key={k} label={k} value={v} pct={v} color={fatigueColor(v)} labelClassName="w-[74px]" valueClassName="w-[26px] text-soft" />;
            })}
          </div>
        </Card>
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <SectionLabel>Tissue load · T(t)</SectionLabel>
            <div className="font-mono text-[10px] leading-none text-dim">local stress, not injury</div>
          </div>
          <TissueBodyMap getT={tissueOf} />
        </Card>
      </div>

      {/* skills — out of the live view for now (no sim skills for authed) */}
      <Card className="px-[22px] py-5">
        <SectionLabel>Skill state</SectionLabel>
        <div className="mt-3 text-[12.5px] font-medium leading-[1.5] text-mute">
          Skill detail is not available in this live view yet.
        </div>
      </Card>
    </>
  );
}

function ReadinessCard({
  isLatest,
  canonicalScore,
  readinessRes,
  meanF,
}: {
  isLatest: boolean;
  canonicalScore: number | null;
  readinessRes: AuthedResource<ReadinessScore>;
  meanF: number;
}) {
  // HISTORICAL snapshot: a neutral, non-scored ring + an honest message and a
  // SEPARATE labeled mean-fatigue metric. Never fill the ring from 100−meanF or
  // inverted fatigue; never borrow readinessWord/readinessColor here.
  if (!isLatest) {
    return (
      <Card className="flex items-center gap-[18px]">
        <NeutralRing />
        <div>
          <SectionLabel className="text-faint">Readiness</SectionLabel>
          <div className="mt-2 text-[12.5px] font-medium leading-[1.5] text-mute">
            Wellness-adjusted readiness was not recorded for this snapshot.
          </div>
          <div className="mt-[11px] font-mono text-[13px] font-semibold leading-none text-soft">
            Mean fatigue · {meanF} / 100
          </div>
        </div>
      </Card>
    );
  }

  // LATEST snapshot with a canonical score → the one backend-owned readiness.
  if (canonicalScore != null) {
    const rc = readinessColor(canonicalScore);
    return (
      <Card className="flex items-center gap-[18px]">
        <ReadinessRing value={Math.round(canonicalScore)} color={rc} />
        <div>
          <SectionLabel className="text-faint">Readiness</SectionLabel>
          <div className="mt-2 text-[18px] font-bold leading-none" style={{ color: rc }}>{readinessWord(canonicalScore)}</div>
          <div className="mt-[9px] text-[11.5px] font-medium leading-[1.5] text-mute">{readinessNote(canonicalScore)}</div>
        </div>
      </Card>
    );
  }

  // LATEST but no score yet (loading / not anchored) — neutral, honest.
  return (
    <Card className="flex items-center gap-[18px]">
      <NeutralRing />
      <div>
        <SectionLabel className="text-faint">Readiness</SectionLabel>
        <div className="mt-2 text-[12.5px] font-medium leading-[1.5] text-mute">
          {readinessPendingNote(readinessRes)}
        </div>
      </div>
    </Card>
  );
}

/**
 * Copy for "latest snapshot, but no canonical score to show". Only reached when
 * no score is in hand, so a refresh still in flight over a scoreless payload
 * reads the same as a first load — which is what the old flag did too.
 */
function readinessPendingNote(resource: AuthedResource<ReadinessScore>): string {
  switch (resource.status) {
    case "loading":
      return "Loading your readiness…";
    case "success":
      return resource.refresh.status === "loading"
        ? "Loading your readiness…"
        : "Readiness isn't available yet — check in or log a workout.";
    case "guest":
    case "error":
      return "Readiness isn't available yet — check in or log a workout.";
    default:
      return assertNever(resource);
  }
}

/** A greyed, non-scored ring shell for historical / unavailable readiness. */
function NeutralRing() {
  return (
    <div className="grid h-[118px] w-[118px] flex-none place-items-center rounded-full" style={{ background: "conic-gradient(rgba(255,255,255,.08) 0 100%)" }}>
      <div className="grid h-[92px] w-[92px] place-items-center rounded-full bg-tile">
        <span className="font-mono text-[26px] font-semibold leading-none text-dim">—</span>
      </div>
    </div>
  );
}

function TwinSkeleton() {
  const bar = "animate-pl-pulse rounded-[14px] bg-white/[0.05]";
  return (
    <div className="flex flex-col gap-[14px]">
      <div className={`${bar} h-[74px]`} />
      <div className="grid grid-cols-1 gap-[14px] lg:grid-cols-[300px_1fr]">
        <div className={`${bar} h-[132px]`} />
        <div className={`${bar} h-[132px]`} />
      </div>
      <div className={`${bar} h-[220px]`} />
      <div className="grid grid-cols-1 gap-[14px] lg:grid-cols-2">
        <div className={`${bar} h-[240px]`} />
        <div className={`${bar} h-[240px]`} />
      </div>
    </div>
  );
}

/**
 * Which single body the card shows, and the summary beside its label. The card
 * frame and header are always present, so the notice markup of <ResourceState>
 * would not fit; this is the exhaustive-switch consumer instead.
 *
 * A refresh in flight over a prescription keeps the summary and swaps the body
 * for "computing"; a failed refresh keeps the summary and shows the
 * unavailable note — exactly what the flat flags produced.
 */
type NextSessionBody =
  | { kind: "none" }
  | { kind: "computing" }
  | { kind: "unavailable" }
  | { kind: "prescription"; rx: WorkoutPrescription };

function nextSessionView(resource: AuthedResource<WorkoutPrescription>): {
  summary: string;
  body: NextSessionBody;
} {
  switch (resource.status) {
    case "guest":
      // Unreachable: the card returns the signed-out hint before rendering.
      return { summary: "", body: { kind: "none" } };
    case "loading":
      return { summary: "loading…", body: { kind: "computing" } };
    case "error":
      return { summary: "", body: { kind: "unavailable" } };
    case "success": {
      const rx = resource.data;
      const summary = `${rx.type} · ${rx.duration_min} min`;
      switch (resource.refresh.status) {
        case "loading":
          return { summary, body: { kind: "computing" } };
        case "error":
          return { summary, body: { kind: "unavailable" } };
        case "idle":
          return { summary, body: { kind: "prescription", rx } };
        default:
          return assertNever(resource.refresh);
      }
    }
    default:
      return assertNever(resource);
  }
}

// Recommended next session — the live prescription from the twin controller
// (GET /v1/next-session), prescribed for the athlete's chosen training goal
// (Settings → Training goal). Authenticated only; guests and unseeded twins
// fall back to a hint instead of fabricating a session.
function NextSessionCard() {
  const { token } = useAuth();
  const { state, actions } = usePerfLab();
  const goal = state.settings.goal;
  const rxRes = useAuthedResource<WorkoutPrescription>((t) => api.getNextSession(goal, t), [goal]);
  const { summary, body } = nextSessionView(rxRes);

  if (!token) {
    return (
      <Card className="px-5 py-4">
        <SectionLabel className="text-faint">Recommended next session</SectionLabel>
        <div className="mt-2 text-[13px] font-medium leading-[1.5] text-mute">
          Sign in to get a live prescription from your twin.
        </div>
      </Card>
    );
  }

  return (
    <Card className="px-[22px] py-5">
      <div className="mb-3 flex items-center justify-between">
        <SectionLabel>Recommended next session</SectionLabel>
        <span className="font-mono text-[10px] leading-none text-dim">
          {summary}
        </span>
      </div>
      {body.kind === "computing" && <div className="text-[13px] font-medium text-mute">Computing your prescription…</div>}
      {body.kind === "unavailable" && (
        <div className="text-[12.5px] font-medium leading-[1.5] text-mute">
          No live prescription yet — log a workout or run a field test to seed your twin.
        </div>
      )}
      {body.kind === "prescription" && (
        <div className="flex flex-col gap-4">
          <div>
            <div className="text-[20px] font-bold leading-tight text-ink">{body.rx.focus}</div>
            <div className="mt-1 text-[12.5px] font-medium leading-[1.5] text-mute">{body.rx.rationale}</div>
          </div>
          {body.rx.exercises && body.rx.exercises.length > 0 && (
            <div className="flex flex-col gap-2 border-t border-white/[0.06] pt-3">
              {body.rx.exercises.map((ex, i) => {
                const detail = [
                  ex.sets != null && ex.reps != null
                    ? `${ex.sets}×${ex.reps}`
                    : ex.reps ?? (ex.sets != null ? `${ex.sets} sets` : ""),
                  ex.load_note,
                ]
                  .filter(Boolean)
                  .join(" · ");
                const tags = ex.weak_point_tags ?? [];
                return (
                  <div key={i} className="flex flex-col gap-[6px]">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-[13px] font-semibold leading-none text-soft">{ex.name}</span>
                      {detail && <span className="font-mono text-[12px] leading-none text-faint">{detail}</span>}
                    </div>
                    <LoadExplanation
                      explanation={ex.load_explanation}
                      exerciseName={ex.name}
                      onOpenAssess={() => actions.setScreen("assess")}
                    />
                    <WeakPointTags tags={tags} />
                  </div>
                );
              })}
            </div>
          )}
          {/* Twin fetched the same prescription as Planning and dropped `why` entirely, so
              the same athlete got a reasoned session on one screen and a bare one here.
              Same component, same contract — the two can no longer disagree. */}
          <WhyThisSession why={body.rx.why} />
        </div>
      )}
    </Card>
  );
}

function ScreenHeaderTwin({ authed, syncLabel, onLog }: { authed: boolean; syncLabel: string; onLog: () => void }) {
  return (
    <header className="flex items-start justify-between gap-5">
      <div>
        <div className="flex items-center gap-[10px]">
          <h1 className="m-0 text-[25px] font-bold leading-none tracking-[-0.02em] text-ink">Digital Twin</h1>
          <Pill>S(t) · v0.3</Pill>
        </div>
        <p className="m-0 mt-[9px] max-w-[440px] text-[13.5px] font-medium leading-[1.5] text-mute">
          Evolving state vector — capacities, fatigue &amp; tissue load.{authed ? "" : " Sample preview until you sign in."}
        </p>
      </div>
      <div className="flex items-center gap-[9px]">
        <SyncChip label={syncLabel} />
        <button onClick={onLog} className="rounded-[9px] bg-ink px-[15px] py-[9px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Log workout</button>
      </div>
    </header>
  );
}
