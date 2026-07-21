// src/perflab/screens/TwinScreen.tsx
//
// The Digital Twin screen. For an AUTHENTICATED athlete this is fully live: the
// state vector, its time-travel scrub, readiness, capacities, fatigue and tissue
// all come from GET /v1/state-history + GET /v1/readiness — no sim, no fabricated
// numbers. A GUEST sees a clearly-labelled preview driven by the deterministic
// sim (sim.ts), which is the ONLY place the VO2 / Profile / Skill mock tiles and
// the simulated Explain drawer appear.
//
// Snapshot identity: cross-screen selection is a snapshot_id (store.selectedTwin
// SnapshotId), never a list index — the state-history window shifts as rows
// accrue. The slider/prev/next operate over the LOCAL index of the loaded window
// and write back the adjacent row's snapshot_id.
import type { ReactNode } from "react";
import * as api from "@/api/perfLabClient";
import { useAuth } from "@/auth/useAuth";
import type { ReadinessScore, StateHistorySnapshotRead, WorkoutPrescription } from "@/types";
import { usePerfLab } from "../store";
import { useAuthedResource, type Resource } from "../useAuthedResource";
import { Card, MetricBar, Pill, ReadinessRing, SectionLabel, SyncChip } from "../ui";
import { Chart, Line, Marker, Radar, useVizTheme } from "../viz";
import { CapacityView } from "./twin/CapacityView";
import {
  CAP_CFG,
  CAP_TIPS,
  COLORS,
  DAYS,
  DAY_COUNT,
  FATIGUE_ORDER,
  fatigueColor,
  readinessColor,
  readinessNote,
  readinessWord,
  SKILL_DEFS,
  swatch,
  swatchLite,
  TISSUE_ORDER,
} from "../sim";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Mean of the six fatigue axes (0–100), decomposed vector when present else the
// legacy scalars. Copied module-local from OverviewScreen (not shared) so the
// Twin owns its own timeline arithmetic.
function meanFatigue(sv: StateHistorySnapshotRead): number {
  const f = sv.fatigue_f;
  const vals = f
    ? [f.cns, f.muscular, f.metabolic, f.structural, f.tendon, f.grip]
    : [sv.f_nm_central, sv.f_nm_peripheral, sv.f_met_systemic, sv.f_struct_damage];
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

/** Compact "Xh ago" for the sync chip. */
function relativeTime(iso: string): string {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** "Viewing" date + relative word from a recorded snapshot's timestamp. */
function viewingLabel(iso: string): { date: string; when: string } {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return { date: iso, when: "" };
  const date = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  const a = new Date();
  a.setHours(0, 0, 0, 0);
  const b = new Date(d);
  b.setHours(0, 0, 0, 0);
  const days = Math.round((a.getTime() - b.getTime()) / 864e5);
  const when = days <= 0 ? "Today" : days === 1 ? "Yesterday" : `${days} days ago`;
  return { date, when };
}

export function TwinScreen() {
  const { token } = useAuth();
  const { state, actions } = usePerfLab();

  const historyRes = useAuthedResource<StateHistorySnapshotRead[]>((t) => api.getStateHistory(t, 60), []);
  const readinessRes = useAuthedResource<ReadinessScore>((t) => api.getReadiness(t), [state.readinessRefreshKey]);

  const isGuest = token == null;
  const rows = historyRes.data;
  const newest = rows && rows.length ? rows[rows.length - 1] : null;
  const syncLabel = isGuest
    ? "Preview"
    : newest
      ? `Synced ${relativeTime(newest.timestamp)}`
      : historyRes.loading
        ? "Syncing…"
        : "No recorded state";

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

// ============================ AUTHENTICATED (LIVE) ============================

function AuthedTwinBody({
  historyRes,
  readinessRes,
}: {
  historyRes: Resource<StateHistorySnapshotRead[]>;
  readinessRes: Resource<ReadinessScore>;
}) {
  const { state, actions } = usePerfLab();
  const { accent } = useVizTheme();
  const rows = historyRes.data;

  // ---- LOADING: neutral skeletons, never sim ----
  if (historyRes.loading && !rows) {
    return <TwinSkeleton />;
  }
  // ---- ERROR: honest retry, distinct from empty (no sim, no manufactured 50s) ----
  if (historyRes.error && !rows) {
    return (
      <Card className="px-[22px] py-8 text-center">
        <div className="text-[14px] font-semibold text-ink">Couldn&apos;t load your twin</div>
        <div className="mx-auto mt-2 max-w-[420px] text-[12.5px] font-medium leading-[1.5] text-mute">{historyRes.error}</div>
        <div className="mt-2 text-[12px] font-medium text-dim">Reload to try again.</div>
      </Card>
    );
  }
  // ---- EMPTY: real empty prompt (never default 50s) ----
  if (!rows || rows.length === 0) {
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
  const canonicalScore = readinessRes.data?.score ?? null;

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
          <TissueBody getT={tissueOf} />
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
  readinessRes: Resource<ReadinessScore>;
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
          {readinessRes.loading ? "Loading your readiness…" : "Readiness isn't available yet — check in or log a workout."}
        </div>
      </div>
    </Card>
  );
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

/** The tissue body-map + region list, fed by a value getter keyed on region label. */
function TissueBody({ getT }: { getT: (label: string) => number }) {
  const reg = (label: string) => {
    const c = fatigueColor(getT(label));
    return { fill: swatch(c), stroke: c };
  };
  const tm = {
    knee: reg("Knee"),
    lumbar: reg("Lumbar"),
    hip: reg("Hip"),
    ankle: reg("Ankle"),
    shoulder: reg("Shoulder"),
    elbow: reg("Elbow"),
    wrist: reg("Wrist"),
    finger: reg("Finger"),
  };
  const tHalo = swatchLite(fatigueColor(getT("Knee")));
  return (
    <>
      <div className="grid grid-cols-[150px_1fr] items-center gap-[18px]">
        <svg viewBox="0 0 130 300" className="block h-auto w-full">
          <g fill="#1b212b" stroke="rgba(255,255,255,.06)" strokeWidth="1">
            <circle cx="65" cy="24" r="14" /><rect x="47" y="42" width="36" height="70" rx="15" /><rect x="28" y="46" width="13" height="74" rx="6.5" /><rect x="89" y="46" width="13" height="74" rx="6.5" /><rect x="45" y="104" width="40" height="26" rx="13" /><rect x="49" y="126" width="14" height="96" rx="7" /><rect x="67" y="126" width="14" height="96" rx="7" />
          </g>
          <circle cx="56" cy="172" r="12" fill={tHalo} className="animate-pl-pulse" /><circle cx="74" cy="172" r="12" fill={tHalo} className="animate-pl-pulse" />
          <g strokeWidth="1.5">
            <circle cx="40" cy="54" r="5.5" fill={tm.shoulder.fill} stroke={tm.shoulder.stroke} /><circle cx="90" cy="54" r="5.5" fill={tm.shoulder.fill} stroke={tm.shoulder.stroke} />
            <circle cx="34" cy="86" r="5.5" fill={tm.elbow.fill} stroke={tm.elbow.stroke} /><circle cx="96" cy="86" r="5.5" fill={tm.elbow.fill} stroke={tm.elbow.stroke} />
            <circle cx="34" cy="114" r="5.5" fill={tm.wrist.fill} stroke={tm.wrist.stroke} /><circle cx="96" cy="114" r="5.5" fill={tm.wrist.fill} stroke={tm.wrist.stroke} />
            <circle cx="34" cy="127" r="4" fill={tm.finger.fill} stroke={tm.finger.stroke} /><circle cx="96" cy="127" r="4" fill={tm.finger.fill} stroke={tm.finger.stroke} />
            <circle cx="65" cy="98" r="6" fill={tm.lumbar.fill} stroke={tm.lumbar.stroke} />
            <circle cx="54" cy="116" r="5.5" fill={tm.hip.fill} stroke={tm.hip.stroke} /><circle cx="76" cy="116" r="5.5" fill={tm.hip.fill} stroke={tm.hip.stroke} />
            <circle cx="56" cy="172" r="6.5" fill={tm.knee.fill} stroke={tm.knee.stroke} /><circle cx="74" cy="172" r="6.5" fill={tm.knee.fill} stroke={tm.knee.stroke} />
            <circle cx="56" cy="214" r="5.5" fill={tm.ankle.fill} stroke={tm.ankle.stroke} /><circle cx="74" cy="214" r="5.5" fill={tm.ankle.fill} stroke={tm.ankle.stroke} />
          </g>
        </svg>
        <div className="flex flex-col gap-[9px]">
          {TISSUE_ORDER.map((k) => {
            const v = getT(k);
            const c = fatigueColor(v);
            return (
              <div key={k} className="flex items-center gap-[9px]">
                <span className="h-[7px] w-[7px] flex-none rounded-[2px]" style={{ background: c }} />
                <span className="flex-1 text-[12px] font-medium leading-none" style={{ color: v >= 45 ? COLORS.soft : COLORS.mute }}>{k}</span>
                <span className="font-mono text-[12px] font-semibold leading-none" style={{ color: v >= 45 ? c : COLORS.soft }}>{v}</span>
              </div>
            );
          })}
        </div>
      </div>
      <div className="mt-4 flex gap-4 border-t border-white/[0.06] pt-[14px] text-[11px] font-medium leading-none text-mute">
        <span><span className="mr-[6px] inline-block h-[8px] w-[8px] rounded-[2px] bg-good" />ready</span>
        <span><span className="mr-[6px] inline-block h-[8px] w-[8px] rounded-[2px] bg-warn" />monitor</span>
        <span><span className="mr-[6px] inline-block h-[8px] w-[8px] rounded-[2px] bg-hot" />load high</span>
      </div>
    </>
  );
}

// ================================ GUEST PREVIEW ================================
// Everything below is the deterministic sim (sim.ts) — a labelled preview of a
// sample athlete. This is the only path that shows VO2 / Profile / Skill mock
// tiles and opens the simulated Explain drawer.

function GuestTwinPreview() {
  const { state, actions } = usePerfLab();
  const { accent } = useVizTheme();
  const N = DAY_COUNT;
  let di = state.twinDayIdx;
  if (di == null || di > N - 1) di = N - 1;
  if (di < 0) di = 0;
  const D = DAYS[di];
  const isToday = di === N - 1;
  const tDate = `${D.date.getDate()} ${MONTHS[D.date.getMonth()]}`;
  const daysAgo = N - 1 - di;
  const tWhen = daysAgo === 0 ? "Today" : daysAgo === 1 ? "Yesterday" : `${daysAgo} days ago`;
  const rc = readinessColor(D.readiness);

  const clampDay = (i: number) => Math.max(0, Math.min(N - 1, i));

  // VO₂ / Profile tiles read the cached field test when viewing today; the sim
  // backs the historical days (and the case where no field test has been run).
  const ft = isToday ? state.fieldTest : null;
  const tVo2 = ft ? ft.vo2_max : D.vo2;
  const tProfileVal = ft ? ft.fatigue_percent : D.profile;
  const tProfileFoot = ft ? ft.fatigue_profile : "endurance-biased";

  const sparkData = DAYS.map((d, i) => [i, d.readiness] as [number, number]);

  const tCaps = CAP_CFG.map((c, idx) => {
    const v = D.C[c.key];
    const pct = Math.max(4, Math.min(100, (v / c.max) * 100));
    const sub = c.base != null ? `+${v - c.base} vs ${c.base}` : c.sub;
    return { label: c.label, val: v, sub, tip: CAP_TIPS[c.key], key: c.key, first: idx === 0, pct };
  });

  const radVals = CAP_CFG.map((c) => Math.max(0.06, Math.min(1, D.C[c.key] / c.max)));
  const radShort = ["Aerobic", "Glyco", "Strength", "Power", "Work"];
  const base0 = DAYS[0].C;
  const radarAxes = CAP_CFG.map((c, k) => ({ key: c.key, label: radShort[k], value: D.C[c.key], max: c.max }));
  const radarBaseline = CAP_CFG.map((c) => base0[c.key]);
  const axisRows = CAP_CFG.map((c, k) => {
    const cur = D.C[c.key];
    const dl = cur - base0[c.key];
    return { label: c.label, val: cur, delta: `${dl >= 0 ? "+" : ""}${dl}`, tip: CAP_TIPS[c.key], pct: Math.round(radVals[k] * 100) };
  });
  const domIdx = radVals.indexOf(Math.max(...radVals));
  const domAxis = CAP_CFG[domIdx].label;
  const typeNames = ["Aerobic engine", "Glycolytic / speed", "Strength-led", "Power-led", "Durability-led"];
  const minN = Math.min(...radVals), maxN = Math.max(...radVals);
  const balPct = Math.round((minN / maxN) * 100);
  const balanceWord = balPct >= 80 ? "Well-rounded" : balPct >= 62 ? "Moderately specialised" : "Highly specialised";
  const composite = Math.round((radVals.reduce((a, b) => a + b, 0) / radVals.length) * 100);
  const profileNote = `Strongest in ${domAxis.toLowerCase()}. ${balPct >= 80 ? "Capacities are evenly developed across all axes." : "Development skews toward the leading axes — room to round out the lower ones."}`;

  const sprog = 0.9 + 0.1 * (di / (N - 1));
  const tSignalTrend = D.signal >= DAYS[Math.max(0, di - 1)].signal ? "↗ rising" : "→ steady";

  const bars = state.capView === "bars";
  const segBtn = (active: boolean) =>
    `rounded-[6px] border-0 px-3 py-[6px] font-mono text-[11px] font-semibold leading-none ${active ? "bg-ink text-[#0a0c10]" : "bg-transparent text-mute"}`;

  const tHalo = swatchLite(fatigueColor(D.T.Knee));
  const reg = (k: string) => {
    const c = fatigueColor(D.T[k]);
    return { fill: swatch(c), stroke: c };
  };
  const tm = { knee: reg("Knee"), lumbar: reg("Lumbar"), hip: reg("Hip"), ankle: reg("Ankle"), shoulder: reg("Shoulder"), elbow: reg("Elbow"), wrist: reg("Wrist"), finger: reg("Finger") };

  return (
    <>
      <div className="flex items-center gap-2 rounded-[12px] border border-mint/25 bg-mint/[0.08] px-4 py-[10px] text-[12px] font-medium leading-none text-[#9ad6c8]">
        <span className="h-[7px] w-[7px] flex-none rounded-full bg-ac" />
        Preview — sample athlete. Sign in to see your own live twin.
      </div>

      {/* time-travel */}
      <Card className="flex items-center gap-[22px] px-5 py-[15px]">
        <div className="min-w-[118px] flex-none">
          <SectionLabel className="text-faint">Viewing</SectionLabel>
          <div className="mt-[7px] flex items-baseline gap-2">
            <span className="text-[18px] font-bold leading-none text-ink">{tDate}</span>
            <span className="text-[11px] font-medium leading-none text-teal">{tWhen}</span>
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <Chart
            width={560}
            height={40}
            padding={{ top: 5, right: 10, bottom: 5, left: 10 }}
            xDomain={[0, N - 1]}
            yDomain={[20, 100]}
            ariaLabel="Sample readiness across the preview window"
            className="h-[38px] w-full"
          >
            <Line data={sparkData} color={accent} opacity={0.7} />
            <Marker x={di} y={D.readiness} color={accent} />
          </Chart>
          <input type="range" min={0} max={N - 1} value={di} onChange={(e) => actions.setTwinDay(+e.target.value)} className="mt-[2px] w-full cursor-pointer" style={{ accentColor: "var(--ac)" }} />
          <div className="mt-[2px] font-mono text-[9px] leading-none text-dim">sample history</div>
        </div>
        <div className="flex flex-none items-center gap-[7px]">
          <button onClick={() => actions.setTwinDay(clampDay(di - 1))} className="h-[34px] w-[34px] rounded-[9px] border border-white/10 bg-white/[0.03] text-[15px] leading-none text-soft">‹</button>
          <button onClick={() => actions.setTwinDay(clampDay(di + 1))} className="h-[34px] w-[34px] rounded-[9px] border border-white/10 bg-white/[0.03] text-[15px] leading-none text-soft">›</button>
          <button onClick={() => actions.setTwinDay(N - 1)} className="rounded-[9px] bg-ink px-[13px] py-[9px] text-[12px] font-semibold leading-none text-[#0a0c10]">Today</button>
        </div>
      </Card>

      {/* readiness + 4 sim tiles */}
      <div className="grid grid-cols-1 gap-[14px] lg:grid-cols-[300px_1fr]">
        <Card className="flex items-center gap-[18px]">
          <ReadinessRing value={D.readiness} color={rc} onClick={() => actions.openExplain("readiness")} />
          <div>
            <SectionLabel className="text-faint">Readiness</SectionLabel>
            <div className="mt-2 text-[18px] font-bold leading-none" style={{ color: rc }}>{readinessWord(D.readiness)}</div>
            <div className="mt-[9px] text-[11.5px] font-medium leading-[1.5] text-mute">{readinessNote(D.readiness)}</div>
          </div>
        </Card>
        <div className="grid grid-cols-2 gap-[14px] lg:grid-cols-4">
          <MiniTile tip="Estimated maximal oxygen uptake (ml·kg⁻¹·min⁻¹) — sample data." label="VO₂max" value={tVo2.toFixed(1)} sub="ml·kg⁻¹·min⁻¹" foot="field test" footColor="text-teal" />
          <MiniTile tip="Speed↔endurance bias — sample data." label="Profile" value={tProfileVal.toFixed(1)} sub="speed ↔ endurance" foot={tProfileFoot} footColor="text-info" />
          <MiniTile label="Habit" value={<>{D.habit}<span className="text-[16px] text-faint">%</span></>} sub="adherence" bar={D.habit} />
          <MiniTile tip="Structural adaptation drive — sample data." label="Struct. signal" value={D.signal.toFixed(1)} sub="adaptation drive" foot={tSignalTrend} footColor="text-teal" />
        </div>
      </div>

      {/* capacities (sim) */}
      <Card className="px-[22px] py-5">
        <div className="mb-[18px] flex items-center justify-between">
          <SectionLabel>Capacities · X(t)</SectionLabel>
          <div className="flex gap-[3px] rounded-[8px] border border-white/[0.08] p-[2px]">
            <button onClick={() => actions.setCapView("bars")} className={segBtn(bars)}>Bars</button>
            <button onClick={() => actions.setCapView("radar")} className={segBtn(!bars)}>Radar</button>
          </div>
        </div>
        {bars ? (
          <div className="grid grid-cols-2 gap-6 md:grid-cols-5">
            {tCaps.map((c) => (
              <div key={c.key} onClick={() => actions.openExplain(`X:${c.key}`)} className={`cursor-pointer ${c.first ? "" : "border-l border-white/[0.05] pl-6"}`}>
                <div data-tip={c.tip} className="mb-2 text-[12px] font-medium leading-none text-mute">{c.label}</div>
                <div className="font-mono text-[30px] font-semibold leading-none text-ink">{c.val}</div>
                <div className="mb-[7px] mt-[11px] h-[6px] overflow-hidden rounded-full bg-white/[0.07]">
                  <div className="h-full rounded-full" style={{ width: `${c.pct}%`, background: "linear-gradient(90deg,var(--ac),#a7e36e)" }} />
                </div>
                <div className="font-mono text-[10px] leading-none text-dim">{c.sub}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 items-center gap-[26px] lg:grid-cols-[280px_1fr_250px]">
            <div>
              <Radar axes={radarAxes} baseline={radarBaseline} size={200} className="mx-auto block h-auto w-full max-w-[220px]" />
              <div className="mt-2 flex justify-center gap-[18px] text-[10px] font-medium leading-none text-mute">
                <span><span className="mr-[5px] inline-block h-[3px] w-[12px] rounded-[2px] bg-ac align-middle" />now</span>
                <span><span className="mr-[5px] inline-block w-[12px] border-t-[1.5px] border-dashed border-white/50 align-middle" />block start</span>
              </div>
            </div>
            <div className="flex flex-col gap-[13px]">
              <div className="flex items-center justify-between font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-dim"><span>Axis · normalized</span><span>vs start</span></div>
              {axisRows.map((a) => (
                <div key={a.label} className="flex items-center gap-3">
                  <span data-tip={a.tip} className="w-[96px] flex-none text-[12px] font-medium leading-none text-mute">{a.label}</span>
                  <span className="w-[40px] flex-none font-mono text-[16px] font-semibold leading-none text-ink">{a.val}</span>
                  <div className="h-[6px] flex-1 overflow-hidden rounded-full bg-white/[0.07]"><div className="h-full rounded-full" style={{ width: `${a.pct}%`, background: "linear-gradient(90deg,var(--ac),#a7e36e)" }} /></div>
                  <span className="w-[34px] text-right font-mono text-[11px] font-semibold leading-none text-teal">{a.delta}</span>
                </div>
              ))}
            </div>
            <div className="flex flex-col justify-center gap-[14px] self-stretch rounded-[14px] border border-white/[0.06] bg-white/[0.02] p-[18px]">
              <div>
                <SectionLabel className="text-faint">Profile shape</SectionLabel>
                <div className="mt-[9px] text-[19px] font-bold leading-[1.1] text-ac">{typeNames[domIdx]}</div>
              </div>
              <div className="flex flex-col gap-[9px]">
                <Row k="Dominant axis" v={domAxis} />
                <Row k="Composite" v={`${composite}`} mono />
                <Row k="Balance" v={balanceWord} />
              </div>
              <div className="border-t border-white/[0.06] pt-3 text-[11px] font-medium leading-[1.5] text-mute">{profileNote}</div>
            </div>
          </div>
        )}
      </Card>

      {/* fatigue + tissue (sim) */}
      <div className="grid grid-cols-1 gap-[14px] lg:grid-cols-2">
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <SectionLabel>Fatigue · F(t)</SectionLabel>
            <div className="font-mono text-[10px] leading-none text-dim">0 fresh → 100 maxed</div>
          </div>
          <div className="flex flex-col gap-[13px]">
            {FATIGUE_ORDER.map((k) => (
              <MetricBar key={k} label={k} value={D.F[k]} pct={D.F[k]} color={fatigueColor(D.F[k])} onClick={() => actions.openExplain(`F:${k}`)} labelClassName="w-[74px]" valueClassName="w-[26px] text-soft" />
            ))}
          </div>
        </Card>
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <SectionLabel>Tissue load · T(t)</SectionLabel>
            <div className="font-mono text-[10px] leading-none text-dim">local stress, not injury</div>
          </div>
          <div className="grid grid-cols-[150px_1fr] items-center gap-[18px]">
            <svg viewBox="0 0 130 300" className="block h-auto w-full">
              <g fill="#1b212b" stroke="rgba(255,255,255,.06)" strokeWidth="1">
                <circle cx="65" cy="24" r="14" /><rect x="47" y="42" width="36" height="70" rx="15" /><rect x="28" y="46" width="13" height="74" rx="6.5" /><rect x="89" y="46" width="13" height="74" rx="6.5" /><rect x="45" y="104" width="40" height="26" rx="13" /><rect x="49" y="126" width="14" height="96" rx="7" /><rect x="67" y="126" width="14" height="96" rx="7" />
              </g>
              <circle cx="56" cy="172" r="12" fill={tHalo} className="animate-pl-pulse" /><circle cx="74" cy="172" r="12" fill={tHalo} className="animate-pl-pulse" />
              <g strokeWidth="1.5">
                <circle cx="40" cy="54" r="5.5" fill={tm.shoulder.fill} stroke={tm.shoulder.stroke} /><circle cx="90" cy="54" r="5.5" fill={tm.shoulder.fill} stroke={tm.shoulder.stroke} />
                <circle cx="34" cy="86" r="5.5" fill={tm.elbow.fill} stroke={tm.elbow.stroke} /><circle cx="96" cy="86" r="5.5" fill={tm.elbow.fill} stroke={tm.elbow.stroke} />
                <circle cx="34" cy="114" r="5.5" fill={tm.wrist.fill} stroke={tm.wrist.stroke} /><circle cx="96" cy="114" r="5.5" fill={tm.wrist.fill} stroke={tm.wrist.stroke} />
                <circle cx="34" cy="127" r="4" fill={tm.finger.fill} stroke={tm.finger.stroke} /><circle cx="96" cy="127" r="4" fill={tm.finger.fill} stroke={tm.finger.stroke} />
                <circle cx="65" cy="98" r="6" fill={tm.lumbar.fill} stroke={tm.lumbar.stroke} />
                <circle cx="54" cy="116" r="5.5" fill={tm.hip.fill} stroke={tm.hip.stroke} /><circle cx="76" cy="116" r="5.5" fill={tm.hip.fill} stroke={tm.hip.stroke} />
                <circle cx="56" cy="172" r="6.5" fill={tm.knee.fill} stroke={tm.knee.stroke} /><circle cx="74" cy="172" r="6.5" fill={tm.knee.fill} stroke={tm.knee.stroke} />
                <circle cx="56" cy="214" r="5.5" fill={tm.ankle.fill} stroke={tm.ankle.stroke} /><circle cx="74" cy="214" r="5.5" fill={tm.ankle.fill} stroke={tm.ankle.stroke} />
              </g>
            </svg>
            <div className="flex flex-col gap-[9px]">
              {TISSUE_ORDER.map((k) => {
                const v = D.T[k];
                const c = fatigueColor(v);
                return (
                  <div key={k} onClick={() => actions.openExplain(`T:${k}`)} className="flex cursor-pointer items-center gap-[9px]">
                    <span className="h-[7px] w-[7px] flex-none rounded-[2px]" style={{ background: c }} />
                    <span className="flex-1 text-[12px] font-medium leading-none" style={{ color: v >= 45 ? COLORS.soft : COLORS.mute }}>{k}</span>
                    <span className="font-mono text-[12px] font-semibold leading-none" style={{ color: v >= 45 ? c : COLORS.soft }}>{v}</span>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="mt-4 flex gap-4 border-t border-white/[0.06] pt-[14px] text-[11px] font-medium leading-none text-mute">
            <span><span className="mr-[6px] inline-block h-[8px] w-[8px] rounded-[2px] bg-good" />ready</span>
            <span><span className="mr-[6px] inline-block h-[8px] w-[8px] rounded-[2px] bg-warn" />monitor</span>
            <span><span className="mr-[6px] inline-block h-[8px] w-[8px] rounded-[2px] bg-hot" />load high</span>
          </div>
        </Card>
      </div>

      {/* skills (sim) */}
      <Card className="px-[22px] py-5">
        <div className="mb-4 flex items-center justify-between">
          <SectionLabel>Skill state</SectionLabel>
          <div data-tip="Skill ratings 0–1 (shown as %) — sample data." className="font-mono text-[10px] leading-none text-dim">0–1 · proficiency</div>
        </div>
        <div className="grid grid-cols-1 gap-x-7 gap-y-[13px] md:grid-cols-2">
          {SKILL_DEFS.map((sd) => {
            const pc = Math.min(100, Math.round(sd.base * sprog * 100));
            return (
              <MetricBar key={sd.label} label={sd.label} value={`${pc}%`} pct={pc} color="linear-gradient(90deg,#45d6c4,#7bd6c0)" labelClassName="w-[118px]" valueClassName="w-[36px] text-[#9ad6c8]" />
            );
          })}
        </div>
      </Card>
    </>
  );
}

// Recommended next session — the live prescription from the twin controller
// (GET /v1/next-session), prescribed for the athlete's chosen training goal
// (Settings → Training goal). Authenticated only; guests and unseeded twins
// fall back to a hint instead of fabricating a session.
function NextSessionCard() {
  const { token } = useAuth();
  const { state } = usePerfLab();
  const goal = state.settings.goal;
  const { data: rx, loading, error } = useAuthedResource<WorkoutPrescription>(
    (t) => api.getNextSession(goal, t),
    [goal],
  );

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
          {rx ? `${rx.type} · ${rx.duration_min} min` : loading ? "loading…" : ""}
        </span>
      </div>
      {loading && <div className="text-[13px] font-medium text-mute">Computing your prescription…</div>}
      {!loading && error && (
        <div className="text-[12.5px] font-medium leading-[1.5] text-mute">
          No live prescription yet — log a workout or run a field test to seed your twin.
        </div>
      )}
      {!loading && !error && rx && (
        <div className="flex flex-col gap-4">
          <div>
            <div className="text-[20px] font-bold leading-tight text-ink">{rx.focus}</div>
            <div className="mt-1 text-[12.5px] font-medium leading-[1.5] text-mute">{rx.rationale}</div>
          </div>
          {rx.exercises && rx.exercises.length > 0 && (
            <div className="flex flex-col gap-2 border-t border-white/[0.06] pt-3">
              {rx.exercises.map((ex, i) => {
                const detail = [
                  ex.sets != null && ex.reps != null
                    ? `${ex.sets}×${ex.reps}`
                    : ex.reps ?? (ex.sets != null ? `${ex.sets} sets` : ""),
                  ex.load_note,
                ]
                  .filter(Boolean)
                  .join(" · ");
                return (
                  <div key={i} className="flex items-baseline justify-between gap-3">
                    <span className="text-[13px] font-semibold leading-none text-soft">{ex.name}</span>
                    {detail && <span className="font-mono text-[12px] leading-none text-faint">{detail}</span>}
                  </div>
                );
              })}
            </div>
          )}
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

function MiniTile({ label, value, sub, foot, footColor, bar, tip }: { label: string; value: ReactNode; sub: string; foot?: string; footColor?: string; bar?: number; tip?: string }) {
  return (
    <Card className="flex flex-col justify-between p-[17px]">
      <div data-tip={tip} className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">{label}</div>
      <div className="my-3">
        <span className="font-mono text-[28px] font-semibold leading-none text-ink">{value}</span>
        <div className="mt-[5px] text-[10px] font-medium leading-none text-faint">{sub}</div>
      </div>
      {bar != null ? (
        <div className="h-[5px] overflow-hidden rounded-full bg-white/[0.08]"><div className="h-full rounded-full bg-ac" style={{ width: `${bar}%` }} /></div>
      ) : (
        <div className={`font-mono text-[10px] leading-none ${footColor}`}>{foot}</div>
      )}
    </Card>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[11px] font-medium leading-none text-mute">{k}</span>
      <span className={`text-[12px] font-semibold leading-none text-ink ${mono ? "font-mono" : ""}`}>{v}</span>
    </div>
  );
}
