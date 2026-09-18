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
const computeMetrics = vi.fn();

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({
    token: TOKEN,
    completeOnboarding: (...args: unknown[]) => completeOnboarding(...args),
  }),
}));

vi.mock("@/api/perfLabClient", () => ({
  computeMetrics: (...args: unknown[]) => computeMetrics(...args),
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
  computeMetrics.mockReset();
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

it("keeps the runner's 1.5 mi time instead of spending it only on the VO₂ estimate", async () => {
  computeMetrics.mockResolvedValue({});
  renderOnboarding({ goal: "Running", units: "Metric (km)" });

  continueStep();
  continueStep();
  type("1.5 mi time", "9:18");
  enter();

  expect((await sentBody()).run_1p5mi_seconds).toBe(558);
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
