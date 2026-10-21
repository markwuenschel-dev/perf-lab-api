// @vitest-environment jsdom
//
// src/perflab/screens/onboardingContext.test.tsx
//
// The defect this file pins: the screen ASKED for training days, session length and (for
// runners) a 1.5 mi time, then dropped every one of them when it built the request — and never
// asked for equipment at all. Equipment is one of the four basics the prescribe gate requires
// (app/logic/onboarding_state.py:72), so a web-onboarded athlete could not be prescribed to.
//
// The invariant, stated once: WHAT THE SCREEN ASKS, THE SCREEN SENDS. The server half — that an
// omitted field is never treated as "clear it" — is proven in tests/test_onboard_preserves_answers.py.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
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

// The client module exports no /compute-metrics caller any more (B2); this mock is the whole
// surface the screen may use.
vi.mock("@/api/perfLabClient", () => ({
  createObjective: (...args: unknown[]) => createObjective(...args),
}));

function ScreenProbe() {
  const { actions } = usePerfLab();
  useEffect(() => {
    actions.setScreen("onboarding");
  }, [actions]);
  return null;
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

/** See onboardingStrength.test.tsx — node's localStorage global shadows jsdom's and throws. */
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

const continueStep = () => fireEvent.click(screen.getByRole("button", { name: "Continue →" }));
const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const enter = () => fireEvent.click(screen.getByRole("button", { name: "Enter Perf Lab →" }));
const press = (name: string) => fireEvent.click(screen.getByRole("button", { name }));

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
  completeOnboarding.mockReset();
  createObjective.mockReset();
  completeOnboarding.mockResolvedValue(undefined);
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 8, 14, 9, 30));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function sentBody(): Promise<Record<string, unknown>> {
  await waitFor(() => expect(completeOnboarding).toHaveBeenCalled());
  return completeOnboarding.mock.calls[0][0] as Record<string, unknown>;
}

it("sends the schedule and equipment the athlete answered", async () => {
  renderOnboarding({ goal: "Strength", units: "Metric (km)" });

  type("Date of birth", "1990-04-17");
  continueStep();
  type("Training days / week", "5");
  type("Session duration (min)", "45");
  press("I have equipment");
  press("Barbell");
  press("Pull-up bar");
  continueStep();
  enter();

  const body = await sentBody();
  expect(body.available_days_per_week).toBe(5);
  expect(body.session_duration_minutes).toBe(45);
  expect(body.equipment).toEqual(["barbell", "pullup_bar"]);
  expect(body.date_of_birth).toBe("1990-04-17");
});

it("sends bodyweight-only as an equipment answer, not as an empty list", async () => {
  renderOnboarding({ goal: "Strength", units: "Metric (km)" });

  continueStep();
  press("Bodyweight only");
  continueStep();
  enter();

  expect((await sentBody()).equipment).toEqual(["bodyweight"]);
});

it("sends [] when the athlete leaves equipment unset — an answer the gate reads as missing", async () => {
  renderOnboarding({ goal: "Strength", units: "Metric (km)" });

  continueStep();
  continueStep();
  enter();

  expect((await sentBody()).equipment).toEqual([]);
});

it("keeps the runner's 1.5 mi time on the profile and as a time trial", async () => {
  renderOnboarding({ goal: "Running", units: "Metric (km)" });

  continueStep();
  continueStep();
  type("1.5 mi time", "9:18");
  enter();

  const body = await sentBody();
  expect(body.run_1p5mi_seconds).toBe(558);
  // Only the time was given: no estimate input is sent, and the stored "sex" setting (which
  // defaults to Female) is never used as one.
  expect(body.run_time_trial).toEqual({ elapsed_seconds: 558 });
});

it("sends the run's estimate inputs explicitly, with the unit the athlete typed, and no 300 m", async () => {
  renderOnboarding({ goal: "Running", units: "Imperial (mi)" });

  type("Date of birth", "1999-02-03");
  continueStep();
  continueStep();
  type("1.5 mi time", "12:00");
  type("Date of the run", "2026-09-01");
  type("Bodyweight (lbs)", "163");
  press("Male");
  enter();

  const body = await sentBody();
  expect(body.run_time_trial).toEqual({
    elapsed_seconds: 720,
    performed_on: "2026-09-01",
    body_mass: 163,
    body_mass_unit: "lb",
    sex_for_estimate: "male",
  });
  expect(JSON.stringify(body)).not.toMatch(/300|time_300m/);
  expect(screen.queryByLabelText("300 m time")).toBeNull();
});

it("sends no run when the athlete leaves the 1.5 mi time blank — nothing is pre-filled", async () => {
  renderOnboarding({ goal: "Running", units: "Metric (km)" });

  continueStep();
  continueStep();
  expect((screen.getByLabelText("1.5 mi time") as HTMLInputElement).value).toBe("");
  enter();

  const body = await sentBody();
  expect(body).not.toHaveProperty("run_time_trial");
  expect(body).not.toHaveProperty("run_1p5mi_seconds");
});

it("refuses a 1.5 mi time it cannot use on the step, before sending anything", async () => {
  renderOnboarding({ goal: "Running", units: "Metric (km)" });

  continueStep();
  continueStep();
  type("1.5 mi time", "2:30");
  enter();

  expect((await screen.findByRole("alert")).textContent).toContain("1.5 mi time");
  expect(completeOnboarding).not.toHaveBeenCalled();
});

function savedRun(overrides: Record<string, unknown> = {}) {
  return {
    observation_id: 7,
    benchmark_code: "run_1p5mi_time_trial",
    performed_on: "2026-09-01",
    recorded_at: "2026-09-14T09:30:00",
    elapsed_seconds: 720,
    status: "estimated",
    reason: null,
    missing_inputs: [],
    vo2max_ml_kg_min: 46.288271875064,
    units: "ml/kg/min",
    headline: "Estimated VO₂max — not used for your plan.",
    population_status: "outside_study_age_range",
    extrapolated: true,
    population_label:
      "Extrapolated and unvalidated: you were outside the study's age range (18–29) when you ran",
    see_ml_kg_min: 3.9,
    model_id: "george1993_1p5mi_run",
    model_version: 1,
    used_for_plan: false,
    ...overrides,
  };
}

it("shows the saved estimate as not used for the plan, labelled extrapolated, with no grade", async () => {
  completeOnboarding.mockResolvedValue({ strength: [], run_time_trial: savedRun() });
  renderOnboarding({ goal: "Running", units: "Metric (km)" });

  continueStep();
  continueStep();
  type("1.5 mi time", "12:00");
  enter();

  const card = await screen.findByRole("region", { name: "Your 1.5 mi run" });
  expect(card.textContent).toContain("46.3");
  expect(card.textContent).toContain("Estimated VO₂max — not used for your plan.");
  expect(card.textContent).toContain("Extrapolated and unvalidated");
  for (const grade of ["Excellent", "Good", "Average", "Poor", "Satisfactory", "Marginal"]) {
    expect(card.textContent).not.toContain(grade);
  }
});

it("shows a saved run without an estimate as saved, and lets the athlete continue", async () => {
  completeOnboarding.mockResolvedValue({
    strength: [],
    run_time_trial: savedRun({
      status: "not_estimated", reason: "missing_inputs", vo2max_ml_kg_min: null,
      missing_inputs: ["body_mass", "sex"],
      headline:
        "Your run is saved without an estimated VO₂max: the estimate needs your body mass and sex, entered with the run.",
    }),
  });
  renderOnboarding({ goal: "Running", units: "Metric (km)" });

  continueStep();
  continueStep();
  type("1.5 mi time", "12:00");
  enter();

  const card = await screen.findByRole("region", { name: "Your 1.5 mi run" });
  expect(card.textContent).toContain("Your run is saved");
  expect(card.textContent).not.toContain("ml·kg⁻¹·min⁻¹");
  expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
});

it("shows a run saved while estimates are unavailable without any value or grade", async () => {
  completeOnboarding.mockResolvedValue({
    strength: [],
    run_time_trial: savedRun({
      status: "not_estimated", reason: "coefficients_unverified", vo2max_ml_kg_min: null,
      elapsed_seconds: 599.6,
      headline: "Your run is saved. VO₂max estimates are not available yet.",
    }),
  });
  renderOnboarding({ goal: "Running", units: "Metric (km)" });

  continueStep();
  continueStep();
  type("1.5 mi time", "9:59");
  enter();

  const card = await screen.findByRole("region", { name: "Your 1.5 mi run" });
  expect(card.textContent).toContain("VO₂max estimates are not available yet.");
  expect(card.textContent).not.toContain("ml·kg⁻¹·min⁻¹");
  expect(card.textContent).not.toContain("not used for your plan");
  // 599.6 s rounds to whole seconds before it is split: 10:00, never 9:60.
  expect(card.textContent).toContain("1.5 mi run · 10:00");
});

it("shows the server's reason when only the run was refused, and the rest was saved", async () => {
  completeOnboarding.mockResolvedValue({
    strength: [],
    run_time_trial: null,
    run_time_trial_refused: "The 1.5 mi run was not saved: its date is in the future.",
  });
  renderOnboarding({ goal: "Running", units: "Metric (km)" });

  continueStep();
  continueStep();
  type("1.5 mi time", "12:00");
  enter();

  const alert = await screen.findByRole("status");
  expect(alert.textContent).toContain("The 1.5 mi run was not saved: its date is in the future.");
  expect(screen.getByRole("heading", { name: "Your baseline is saved" })).toBeTruthy();
});

it("un-selecting the sex sends no sex input at all", async () => {
  renderOnboarding({ goal: "Running", units: "Metric (km)" });

  continueStep();
  continueStep();
  type("1.5 mi time", "12:00");
  press("Male");
  press("Male");
  enter();

  const body = await sentBody();
  expect(body.run_time_trial).toEqual({ elapsed_seconds: 720 });
  expect(screen.queryByRole("button", { name: "Male", pressed: true })).toBeNull();
});

it.each([
  ["Metric (km)", "Bodyweight (kg)", "24", "Bodyweight: enter between 25 and 300 kg."],
  ["Metric (km)", "Bodyweight (kg)", "301", "Bodyweight: enter between 25 and 300 kg."],
  ["Imperial (mi)", "Bodyweight (lbs)", "700", "Bodyweight: enter between 55.2 and 661.3 lbs."],
])("refuses an implausible bodyweight on the step (%s, %s %s)", async (units, label, value, message) => {
  renderOnboarding({ goal: "Running", units });

  continueStep();
  continueStep();
  type("1.5 mi time", "12:00");
  type(label, value);
  enter();

  expect((await screen.findByRole("alert")).textContent).toContain(message);
  expect(completeOnboarding).not.toHaveBeenCalled();
});

it("keeps a bodyweight's precision when the athlete switches units", async () => {
  renderOnboarding({ goal: "Running", units: "Metric (km)" });

  continueStep();
  continueStep();
  type("Bodyweight (kg)", "80");
  press("← Back");
  press("Imperial (mi)");
  continueStep();

  expect((screen.getByLabelText("Bodyweight (lbs)") as HTMLInputElement).value).toBe("176.4");
});

it("limits the run date to the device's local today", () => {
  vi.setSystemTime(new Date(2026, 8, 14, 0, 30)); // just after local midnight
  renderOnboarding({ goal: "Running", units: "Metric (km)" });

  continueStep();
  continueStep();

  expect((screen.getByLabelText("Date of the run") as HTMLInputElement).max).toBe("2026-09-14");
});

it("sends the optional context fields, converted to metric when the athlete is imperial", async () => {
  renderOnboarding({ goal: "General", units: "Imperial (mi)" });

  continueStep();
  continueStep();
  press("Advanced");
  type("Years training", "6");
  type("Height (in)", "70");
  type("Max pull-ups", "15");
  enter();

  const body = await sentBody();
  expect(body.experience_level).toBe("advanced");
  expect(body.experience_years).toBe(6);
  expect(body.height_cm).toBeCloseTo(177.8, 1);
  expect(body.pullup_max_reps).toBe(15);
});

it("omits optional fields the athlete left blank, so the server keeps what it has", async () => {
  renderOnboarding({ goal: "Strength", units: "Metric (km)" });

  continueStep();
  continueStep();
  enter();

  const body = await sentBody();
  for (const key of ["experience_level", "experience_years", "height_cm", "overhead_1rm_kg", "pullup_max_reps"]) {
    expect(body).not.toHaveProperty(key);
  }
});
