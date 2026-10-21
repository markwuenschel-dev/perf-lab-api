// src/perflab/prescription/MeasurementRecommendations.tsx
//
// "What to measure next" — `why.measurement_recommendations`, rendered on Assess.
//
// The data arrives on the PRESCRIPTION but belongs on ASSESS: the backend's framing is
// that the honest response to low confidence is to say what would raise it, and Assess is
// the screen where an athlete acts on that. So this panel fetches the prescription itself
// (the `OnboardingBanner` precedent on the same screen: a self-contained component owning
// its own resource) rather than making the catalog screen aware of the planner.
//
// It renders nothing at all — not an empty state — when there is nothing to ask for.
// An empty list means every capacity axis is already established, which is good news and
// needs no card; a guest, a failed load or a twin with no state are all likewise silent,
// because a measurement ask is only meaningful against a real prescription.

import { useAuthedResource } from "../useAuthedResource";
import { usePerfLab } from "../store";
import * as api from "../../api/perfLabClient";
import { Card } from "../ui";
import { axisLabel, BAND, BAND_CHIP, narrowStatus } from "./axes";
import type {
  AssessmentSurfaceRead,
  MeasurementRecommendation,
  WorkoutPrescription,
} from "@/types";

/**
 * Which benchmarks would actually measure this axis.
 *
 * `AssessmentBenchmarkCard.measures_axes` is keyed by the same capacity-axis vocabulary
 * as the recommendation's `axis`, so the link needs no lookup table and no new backend
 * field — but it is built from whatever catalog the screen already loaded, so a mode
 * showing a narrower catalog legitimately finds none, and the row then simply names the
 * axis without pretending to route the athlete somewhere.
 */
function benchmarksFor(axis: string, surface: AssessmentSurfaceRead | null): string[] {
  if (surface == null) return [];
  return surface.groups
    .flatMap((g) => g.cards)
    .filter((c) => c.measures_axes.includes(axis))
    .map((c) => c.name);
}

function RecommendationRow({
  rec,
  surface,
}: {
  rec: MeasurementRecommendation;
  surface: AssessmentSurfaceRead | null;
}) {
  const band = BAND[narrowStatus(rec.current_status)];
  const benchmarks = benchmarksFor(rec.axis, surface);

  return (
    <div className="flex flex-col gap-[6px] border-t border-white/[0.06] pt-[11px] first:border-0 first:pt-0">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-[5px]">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-semibold leading-none text-ink">{axisLabel(rec.axis)}</span>
          <span className={`${BAND_CHIP} ${band.cls}`}>{band.label}</span>
          {rec.material_to_goal && (
            <span className="rounded-full border border-ac/30 bg-ac/[0.1] px-2 py-[3px] text-[9.5px] font-semibold uppercase leading-none tracking-[0.08em] text-ac">
              your goal
            </span>
          )}
        </div>
      </div>
      <div className="text-[11.5px] font-medium leading-[1.5] text-mute">{rec.reason}</div>
      {benchmarks.length > 0 && (
        <div className="font-mono text-[10px] leading-[1.45] text-dim">
          measured by {benchmarks.join(", ")}
        </div>
      )}
    </div>
  );
}

export function MeasurementRecommendations({ surface }: { surface: AssessmentSurfaceRead | null }) {
  const { state } = usePerfLab();
  const goal = state.settings.goal;
  const rxRes = useAuthedResource<WorkoutPrescription>((t) => api.getNextSession(goal, t), [goal], { calendar: true });

  // Exhaustive on purpose rather than <ResourceState>: every non-success arm renders
  // nothing, so a notice would be noise on a screen that already has its own twin banner.
  if (rxRes.status !== "success") return null;

  const recs = rxRes.data.why?.measurement_recommendations ?? [];
  if (recs.length === 0) return null;

  // Goal-relevant axes first: the backend ranks them, and re-sorting would discard that,
  // so this only stabilises ties without reordering the engine's judgement.
  const ordered = [...recs].sort(
    (a, b) => Number(b.material_to_goal) - Number(a.material_to_goal),
  );

  return (
    <Card className="flex flex-col gap-3 p-[18px]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="text-[13px] font-semibold leading-none text-ink">What to measure next</span>
        <span className="font-mono text-[10px] leading-none text-dim">
          would sharpen your current plan
        </span>
      </div>
      <div className="flex flex-col gap-[11px]">
        {ordered.map((rec, i) => (
          <RecommendationRow key={`${rec.axis}-${i}`} rec={rec} surface={surface} />
        ))}
      </div>
    </Card>
  );
}
