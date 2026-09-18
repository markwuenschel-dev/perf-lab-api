// @vitest-environment jsdom
//
// src/perflab/screens/onboardingStrength.test.tsx
//
// Onboarding's strength step, rendered as the athlete meets it: the REAL store (units, step,
// screen), the real form and body builder, and a mocked auth hook whose completeOnboarding is
// the last frontend seam before POST /v1/onboard. strengthEvidenceBody.test.ts proves the
// builder; this proves the SCREEN hands the builder the selected unit, keeps the athlete on the
// step with their entries when saving fails, and only leaves the step once saving succeeded.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { lbsToKg } from "@/lib/units";
import { PerfLabProvider } from "../PerfLabProvider";
import { STORAGE_KEY, usePerfLab, type Settings } from "../store";
import { OnboardingScreen } from "./OnboardingScreen";

const TOKEN = "athlete-token";
const completeOnboarding = vi.fn();
const createObjective = vi.fn();

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({
    token: TOKEN,
    completeOnboarding: (...args: unknown[]) => completeOnboarding(...args),
  }),
}));

vi.mock("@/api/perfLabClient", () => ({
  computeMetrics: vi.fn(),
  createObjective: (...args: unknown[]) => createObjective(...args),
}));

/** Puts the store on the onboarding screen, as App does after register, and shows where it is. */
function ScreenProbe() {
  const { state, actions } = usePerfLab();
  useEffect(() => {
    actions.setScreen("onboarding");
  }, [actions]);
  return <span data-testid="screen">{state.screen}</span>;
}

function renderOnboarding(settings: Partial<Settings>) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ settings }));
  render(
    <PerfLabProvider>
      <ScreenProbe />
      <OnboardingScreen />
    </PerfLabProvider>,
  );
}

const continueStep = () => fireEvent.click(screen.getByRole("button", { name: "Continue →" }));
const back = () => fireEvent.click(screen.getByRole("button", { name: "← Back" }));
const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const valueOf = (label: string) => (screen.getByLabelText(label) as HTMLInputElement).value;
const enter = () => fireEvent.click(screen.getByRole("button", { name: "Enter Perf Lab →" }));

function toStep3() {
  continueStep();
  continueStep();
  screen.getByText("Step 3 of 3");
}

function chooseSet(lift: string) {
  const group = screen.getByRole("radiogroup", { name: `${lift}: How did you get this number?` });
  fireEvent.click(within(group).getByRole("radio", { name: "A set I did" }));
}

/** An in-memory Storage. Node's own `localStorage` global shadows jsdom's and throws without a
 *  `--localstorage-file`, so the store's persisted settings are seeded through this instead. */
function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => {
      data.delete(key);
    },
    setItem: (key, value) => {
      data.set(key, String(value));
    },
  };
}

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
  completeOnboarding.mockReset();
  createObjective.mockReset();
  // Only Date is faked: the report builder refuses future days, so "today" must be fixed.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 8, 14, 9, 30));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("an imperial athlete's lifts reach the API in kilograms", () => {
  it("converts a tested max and a set's load from pounds, and sends nothing in pounds", async () => {
    completeOnboarding.mockResolvedValue(undefined);
    renderOnboarding({ goal: "Strength", units: "Imperial (mi)" });
    toStep3();

    type("Squat: Weight in lb", "315");
    type("Squat: Date performed", "2026-09-10");
    chooseSet("Deadlift");
    type("Deadlift: Load in lb", "225");
    type("Deadlift: Reps", "3");
    type("Deadlift: Effort as RPE 1 to 10, optional", "9");
    type("Deadlift: Date performed", "2026-09-08");
    enter();

    await waitFor(() => expect(completeOnboarding).toHaveBeenCalledTimes(1));
    expect(completeOnboarding).toHaveBeenCalledWith({
      goal: "Strength",
      // The training context the screen collected on step 2 now travels with the request —
      // these are its untouched defaults here. onboardingContext.test.tsx owns that contract;
      // they appear in this exact-payload assertion so it keeps proving nothing ELSE is sent.
      equipment: [],
      available_days_per_week: 4,
      session_duration_minutes: 60,
      strength: [
        {
          benchmark_code: "pl_e1rm_squat",
          method: "tested_max",
          value_kg: lbsToKg(315),
          performed_at: new Date(2026, 8, 10).toISOString(),
        },
        {
          benchmark_code: "pl_e1rm_deadlift",
          method: "rep_set",
          load_kg: lbsToKg(225),
          reps: 3,
          rpe: 9,
          performed_at: new Date(2026, 8, 8).toISOString(),
        },
      ],
    });
  });
});

describe("switching units converts what is already typed", () => {
  it("re-expresses each lift's weight and set load, leaves blanks and reps alone, and ignores a click on the current unit", () => {
    renderOnboarding({ goal: "Strength", units: "Metric (km)" });
    toStep3();
    type("Squat: Weight in kg", "140");
    chooseSet("Deadlift");
    type("Deadlift: Load in kg", "100");
    type("Deadlift: Reps", "3");

    back();
    fireEvent.click(screen.getByRole("button", { name: "Imperial (mi)" }));
    continueStep();
    expect(valueOf("Squat: Weight in lb")).toBe("309");
    expect(valueOf("Deadlift: Load in lb")).toBe("220");
    expect(valueOf("Deadlift: Reps")).toBe("3");
    expect(valueOf("Bench press: Weight in lb")).toBe("");

    back();
    fireEvent.click(screen.getByRole("button", { name: "Imperial (mi)" }));
    continueStep();
    expect(valueOf("Squat: Weight in lb")).toBe("309");

    back();
    fireEvent.click(screen.getByRole("button", { name: "Metric (km)" }));
    continueStep();
    expect(valueOf("Squat: Weight in kg")).toBe("140.2");
  });
});

describe("leaving the step", () => {
  it("goes to Overview only after the baseline saved", async () => {
    let resolveSave: () => void = () => undefined;
    completeOnboarding.mockImplementation(
      () => new Promise<void>((resolve) => { resolveSave = resolve; }),
    );
    renderOnboarding({ goal: "Strength", units: "Metric (km)" });
    await waitFor(() => expect(screen.getByTestId("screen").textContent).toBe("onboarding"));
    toStep3();
    type("Squat: Weight in kg", "140");
    type("Squat: Date performed", "2026-09-10");
    enter();

    await screen.findByRole("button", { name: "Seeding twin…" });
    expect(screen.getByTestId("screen").textContent).toBe("onboarding");

    resolveSave();
    await waitFor(() => expect(screen.getByTestId("screen").textContent).toBe("overview"));
  });

  it("keeps the athlete on the step, with their entries, when saving fails — and a retry can succeed", async () => {
    completeOnboarding
      .mockRejectedValueOnce({ message: "Onboarding was refused.", status: 422 })
      .mockResolvedValueOnce(undefined);
    renderOnboarding({ goal: "Strength", units: "Imperial (mi)" });
    await waitFor(() => expect(screen.getByTestId("screen").textContent).toBe("onboarding"));
    toStep3();
    type("Squat: Weight in lb", "315");
    type("Squat: Date performed", "2026-09-10");
    enter();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Onboarding was refused.");
    expect(screen.getByTestId("screen").textContent).toBe("onboarding");
    screen.getByText("Step 3 of 3");
    expect(valueOf("Squat: Weight in lb")).toBe("315");
    expect(valueOf("Squat: Date performed")).toBe("2026-09-10");

    enter();
    await waitFor(() => expect(screen.getByTestId("screen").textContent).toBe("overview"));
    expect(completeOnboarding).toHaveBeenCalledTimes(2);
    expect(completeOnboarding.mock.calls[1]).toEqual(completeOnboarding.mock.calls[0]);
  });

  it("does not send an incomplete report — it names the lift and what is missing", () => {
    renderOnboarding({ goal: "Strength", units: "Metric (km)" });
    toStep3();
    type("Squat: Weight in kg", "140");
    enter();

    expect(screen.getByRole("alert").textContent).toBe("Squat: Add the date you did it.");
    expect(completeOnboarding).not.toHaveBeenCalled();
    screen.getByText("Step 3 of 3");
  });
});
