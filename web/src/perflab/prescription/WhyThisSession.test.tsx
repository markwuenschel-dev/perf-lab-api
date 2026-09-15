// @vitest-environment jsdom
//
// The rules this component must not break, all of them honesty rules rather than
// layout ones:
//   • engine codes are never shown — labels come from `constraint_details`, and a payload
//     stored before labels existed gets one neutral line, not raw codes;
//   • bookkeeping marked not athlete-visible stays hidden, while a safety adjustment shows;
//   • with state present and no fatigue/tissue rule firing, the lead says only that — and what
//     has not been measured is listed separately;
//   • evidence REPLACES the driver phrases, never doubles them;
//   • a missing per-axis confidence band means UNKNOWN certainty, so nothing may be shown;
//   • families with no variance are named as having no estimate, not as unreliable.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { NO_ADJUSTMENT_LEAD, UNLABELLED_RULES_LINE, WhyThisSession } from "./WhyThisSession";
import type { StateEvidence, WorkoutPrescription } from "@/types";

afterEach(cleanup);

type Why = NonNullable<WorkoutPrescription["why"]>;
type Detail = NonNullable<Why["constraint_details"]>[number];

const why = (over: Partial<Why> = {}): Why => ({ ...over }) as Why;

const evidence = (over: Partial<StateEvidence> = {}): StateEvidence =>
  ({
    axis: "f_nm_central",
    label: "elevated CNS / central fatigue",
    value: 62.4,
    threshold: 55,
    direction: "above",
    ...over,
  }) as StateEvidence;

const detail = (code: string, label: string, group: Detail["group"], athlete_visible = true): Detail => ({
  code,
  label,
  group,
  athlete_visible,
});

describe("WhyThisSession", () => {
  it("renders nothing at all when there is no explanation", () => {
    const { container } = render(<WhyThisSession why={undefined} />);
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing when the explanation carries no readable content", () => {
    const { container } = render(<WhyThisSession why={why({ state_drivers: [], constraints_applied: [] })} />);
    expect(container.innerHTML).toBe("");
  });

  it("shows labels, never the engine codes", () => {
    render(
      <WhyThisSession
        why={why({
          goal_alignment: "Hypertrophy",
          constraints_applied: ["block:phase=accumulation(×1.15)", "equipment:unconfigured"],
          constraint_details: [
            detail("block:phase=accumulation(×1.15)", "Accumulation phase: session length ×1.15.", "block"),
            detail("equipment:unconfigured", "Equipment not set, so any exercise may appear. Set it in Settings.", "equipment"),
          ],
        })}
      />,
    );
    expect(screen.getByText("Built for Hypertrophy.")).toBeTruthy();
    expect(screen.getByText("Accumulation phase: session length ×1.15.")).toBeTruthy();
    expect(screen.queryByText(/block:phase/)).toBeNull();
    expect(screen.queryByText(/equipment:unconfigured/)).toBeNull();
  });

  it("hides bookkeeping but shows a safety adjustment first", () => {
    render(
      <WhyThisSession
        why={why({
          constraints_applied: ["block:benchmark", "static_with_safety_caps:arm", "safety:override=safety_regional_tissue"],
          constraint_details: [
            detail("block:benchmark", "Benchmark session in your plan.", "block"),
            detail("static_with_safety_caps:arm", "Fixed-template experiment arm.", "internal", false),
            detail("safety:override=safety_regional_tissue", "Safety override: tissue stress is high.", "safety"),
          ],
        })}
      />,
    );
    expect(screen.queryByText("Fixed-template experiment arm.")).toBeNull();
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["Safety override: tissue stress is high.", "Benchmark session in your plan."]);
  });

  it("keeps advisories apart from what shaped the session", () => {
    render(
      <WhyThisSession
        why={why({
          constraints_applied: ["Aerobic base low — bias threshold before VO2"],
          constraint_details: [
            detail("Aerobic base low — bias threshold before VO2", "Aerobic base is low, so threshold work comes before VO2 work.", "advisory"),
          ],
        })}
      />,
    );
    expect(screen.getByText("Noted, not applied")).toBeTruthy();
    expect(screen.queryByText("What shaped this session")).toBeNull();
  });

  it("shows one neutral line for a prescription stored before labels existed", () => {
    render(<WhyThisSession why={why({ constraints_applied: ["block:rpe_target=8"] })} />);
    expect(screen.getByText(UNLABELLED_RULES_LINE)).toBeTruthy();
    expect(screen.queryByText(/rpe_target/)).toBeNull();
  });

  it("says only that no fatigue or tissue rule adjusted anything, and lists what is unmeasured separately", () => {
    render(
      <WhyThisSession
        why={why({
          state_drivers: ["no additional adjustment from the available fatigue and tissue signals"],
          confidence: {
            policy_version: "v1",
            capacity_axes: { aerobic: "provisional", mobility: "insufficient", skill: "insufficient" },
          },
        })}
      />,
    );
    expect(screen.getByText(NO_ADJUSTMENT_LEAD)).toBeTruthy();
    expect(screen.getByText(/Not yet measured:/).textContent).toBe("Not yet measured: Mobility and Skill");
    expect(screen.queryByText(/normal twin bands/)).toBeNull();
  });

  it("shows the driver phrases when there is no state to report on", () => {
    render(<WhyThisSession why={why({ state_drivers: ["No AthleteState history — baseline not established"] })} />);
    expect(screen.getByText("No AthleteState history — baseline not established")).toBeTruthy();
    expect(screen.queryByText(NO_ADJUSTMENT_LEAD)).toBeNull();
  });

  it("shows evidence INSTEAD OF the phrases, never both", () => {
    render(
      <WhyThisSession
        why={why({
          state_drivers: ["elevated CNS / central fatigue"],
          state_evidence: [evidence()],
        })}
      />,
    );
    expect(screen.getAllByText("elevated CNS / central fatigue")).toHaveLength(1);
    expect(screen.getByText("62.4")).toBeTruthy();
    expect(screen.getByText(/55/)).toBeTruthy();
    expect(screen.queryByText(NO_ADJUSTMENT_LEAD)).toBeNull();
  });

  it("shows no certainty chip for an axis the engine models no variance for", () => {
    render(<WhyThisSession why={why({ state_evidence: [evidence({ confidence_status: null })] })} />);
    expect(screen.queryByText("measured")).toBeNull();
    expect(screen.queryByText("provisional")).toBeNull();
    expect(screen.queryByText("unmeasured")).toBeNull();
  });

  it("shows the band when the axis does carry one", () => {
    render(<WhyThisSession why={why({ state_evidence: [evidence({ confidence_status: "provisional" })] })} />);
    expect(screen.getByText("provisional")).toBeTruthy();
  });

  it("names the families with no uncertainty estimate, without calling them unreliable", () => {
    render(
      <WhyThisSession
        why={why({
          confidence: {
            policy_version: "v1",
            capacity_axes: { aerobic: "provisional" },
            uncertainty_not_modelled: ["fatigue_f", "tissue_t", "skill_state"],
          },
        })}
      />,
    );
    expect(screen.getByText("Fatigue, tissue and skill have no uncertainty estimate yet.")).toBeTruthy();
    expect(screen.queryByText(/reliable/)).toBeNull();
  });

  it("groups the axes into one row per band", () => {
    render(
      <WhyThisSession
        why={why({
          confidence: {
            policy_version: "v1",
            capacity_axes: { aerobic: "provisional", power: "provisional", max_strength: "established" },
          },
        })}
      />,
    );
    expect(screen.getByText("Aerobic, Power")).toBeTruthy();
    expect(screen.getByText("Max strength")).toBeTruthy();
  });

  it("summarises with the least certain axis and its band", () => {
    render(
      <WhyThisSession
        why={why({
          confidence: {
            policy_version: "v1",
            weakest_capacity_axis: "max_strength",
            weakest_capacity_status: "insufficient",
          },
        })}
      />,
    );
    expect(screen.getByText("Max strength")).toBeTruthy();
    expect(screen.getByText("unmeasured")).toBeTruthy();
  });

  it("survives a confidence band this build has never heard of", () => {
    render(
      <WhyThisSession
        why={why({
          confidence: {
            policy_version: "v1",
            capacity_axes: { aerobic: "wildly_confident" as never },
          },
        })}
      />,
    );
    expect(screen.getAllByText(/unmeasured/).length).toBeGreaterThan(0);
  });
});
