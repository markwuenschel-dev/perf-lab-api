// @vitest-environment jsdom
//
// src/perflab/screens/settingsStrength.test.tsx
//
// Settings → Performance profile → Strength, rendered as a whole screen (the editor is not
// exported). A lift is updated by sending a characterized report, never by overwriting the
// profile number; the displayed value is the server's projection, so after a save the screen
// must READ IT BACK rather than echo what was typed. Weights are shown and typed in the
// selected unit and reach the API in kilograms.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { lbsToKg } from "@/lib/units";
import type { ProfileRead, StrengthOutcome, UnderivedStrengthReportRead } from "@/types";
import { SettingsScreen } from "./SettingsScreen";

const TOKEN = "athlete-token";
const getProfile = vi.fn();
const submitStrengthEvidence = vi.fn();
const listStrengthReports = vi.fn();
const refreshProfile = vi.fn();

vi.mock("@/api/perfLabClient", () => ({
  getProfile: (...args: unknown[]) => getProfile(...args),
  submitStrengthEvidence: (...args: unknown[]) => submitStrengthEvidence(...args),
  listStrengthReports: (...args: unknown[]) => listStrengthReports(...args),
  listObjectives: () => Promise.resolve([]),
  getWearableConnection: () => Promise.resolve({ connected: false, connection: null }),
  listIngestTokens: () => Promise.resolve([]),
  wellnessIngestUrl: () => "/v1/wellness/ingest",
  createIngestToken: vi.fn(),
  revokeIngestToken: vi.fn(),
  updateProfile: vi.fn(),
  createObjective: vi.fn(),
  deleteObjective: vi.fn(),
}));

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({
    token: TOKEN,
    isAuthenticated: true,
    isGuest: false,
    profile: null,
    user: { email: "athlete@example.com" },
    email: "athlete@example.com",
    refreshProfile: (...args: unknown[]) => refreshProfile(...args),
    logout: vi.fn(),
  }),
}));

let units = "Metric (km)";
const storeActions = { setSetting: vi.fn(), refreshObjectives: vi.fn(), openAuth: vi.fn() };

vi.mock("../store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../store")>();
  return {
    ...actual,
    usePerfLab: () => ({
      state: {
        settings: {
          sex: "Female", units, accent: "#c6f135", goal: "Strength",
          notifReadiness: true, notifTissue: true, notifWeekly: false,
        },
        objectivesRefreshKey: 0,
      },
      actions: storeActions,
    }),
  };
});

const profile = (over: Partial<ProfileRead> = {}): ProfileRead => ({
  available_days_per_week: 4,
  bench_1rm_kg: null,
  bodyweight_kg: null,
  deadlift_1rm_kg: null,
  display_name: null,
  equipment: [],
  experience_level: "intermediate",
  experience_years: 3,
  height_cm: null,
  overhead_1rm_kg: null,
  primary_goal: null,
  pullup_max_reps: null,
  run_1p5mi_seconds: null,
  run_5k_seconds: null,
  session_duration_minutes: 60,
  squat_1rm_kg: null,
  ...over,
});

/** The Squat row: its label, its current value, and its Update button. */
const squatRow = () => within(screen.getByText("Squat").parentElement as HTMLElement);

async function openSquat(shown: string) {
  await waitFor(() => squatRow().getByText(shown));
  fireEvent.click(squatRow().getByRole("button", { name: "Update" }));
}

beforeEach(() => {
  units = "Metric (km)";
  getProfile.mockReset();
  submitStrengthEvidence.mockReset();
  listStrengthReports.mockReset();
  listStrengthReports.mockResolvedValue([]);
  refreshProfile.mockReset();
  refreshProfile.mockResolvedValue(undefined);
});

afterEach(cleanup);

/** What POST /v1/benchmarks/strength-evidence returns for a recorded report (StrengthOutcome). */
function recorded(code: string, rawValue: number, id = 41): StrengthOutcome {
  return {
    outcome: "recorded",
    reason: null,
    value_available: true,
    observation: {
      id, user_id: 7, benchmark_definition_id: 3, benchmark_code: code,
      observed_at: "2026-10-12T10:00:00", raw_value: rawValue, secondary_value: null,
      normalized_value: null, validity_status: "valid", source: "manual",
      state_disposition: "applied", state_disposition_reason: null,
    },
    report: null,
  };
}

const SQUAT_OK = recorded("pl_e1rm_squat", 150);

describe("updating a lift in Settings", () => {
  it("sends a report with the session, then shows the projection read back from the server", async () => {
    getProfile
      .mockResolvedValueOnce(profile({ squat_1rm_kg: 140 }))
      .mockResolvedValueOnce(profile({ squat_1rm_kg: 150 }));
    submitStrengthEvidence.mockResolvedValue(SQUAT_OK);
    render(<SettingsScreen />);

    await openSquat("140 kg");
    fireEvent.change(screen.getByLabelText("Squat: Weight in kg"), { target: { value: "150" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => squatRow().getByText("150 kg"));
    expect(submitStrengthEvidence).toHaveBeenCalledWith(
      {
        benchmark_code: "pl_e1rm_squat", method: "tested_max", value_kg: 150, collection_mode: "retest",
        idempotency_key: expect.any(String),
      },
      TOKEN,
    );
    expect(getProfile).toHaveBeenCalledTimes(2);
    expect(getProfile).toHaveBeenLastCalledWith(TOKEN);
    expect(refreshProfile).toHaveBeenCalled();
    expect(screen.queryByLabelText("Squat: Weight in kg")).toBeNull();
  });

  it("keeps the form open with the error, and reloads nothing, when the report is refused", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    submitStrengthEvidence.mockRejectedValue({ message: "Report refused.", status: 422 });
    render(<SettingsScreen />);

    await openSquat("140 kg");
    fireEvent.change(screen.getByLabelText("Squat: Weight in kg"), { target: { value: "150" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("Report refused.");
    expect((screen.getByLabelText("Squat: Weight in kg") as HTMLInputElement).value).toBe("150");
    expect(getProfile).toHaveBeenCalledTimes(1);
    squatRow().getByText("140 kg");
  });
});

describe("units in Settings", () => {
  it("shows and takes pounds for an imperial athlete, and sends kilograms", async () => {
    units = "Imperial (mi)";
    getProfile
      .mockResolvedValueOnce(profile({ squat_1rm_kg: 140 }))
      .mockResolvedValueOnce(profile({ squat_1rm_kg: 142.9 }));
    submitStrengthEvidence.mockResolvedValue(SQUAT_OK);
    render(<SettingsScreen />);

    await openSquat("309 lb");
    fireEvent.change(screen.getByLabelText("Squat: Weight in lb"), { target: { value: "315" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => squatRow().getByText("315 lb"));
    expect(submitStrengthEvidence).toHaveBeenCalledWith(
      {
        benchmark_code: "pl_e1rm_squat",
        method: "tested_max",
        value_kg: lbsToKg(315),
        collection_mode: "retest",
        idempotency_key: expect.any(String),
      },
      TOKEN,
    );
  });

  it("keeps an open report in the unit it was typed in when the unit setting changes", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    submitStrengthEvidence.mockResolvedValue(SQUAT_OK);
    const { rerender } = render(<SettingsScreen />);

    await openSquat("140 kg");
    fireEvent.change(screen.getByLabelText("Squat: Weight in kg"), { target: { value: "150" } });
    units = "Imperial (mi)";
    rerender(<SettingsScreen />);
    screen.getByLabelText("Squat: Weight in kg");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(1));
    expect(submitStrengthEvidence.mock.calls[0][0]).toMatchObject({ value_kg: 150 });
  });
});

const keyOf = (call: number) => submitStrengthEvidence.mock.calls[call][0].idempotency_key as string;

async function saveSquat(value: string) {
  fireEvent.change(screen.getByLabelText("Squat: Weight in kg"), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
}

const liftRow = (label: string) => within(screen.getByRole("group", { name: label }));
const UNCONFIRMED = /wasn't confirmed, so it may already be recorded/;
const uncertain = { message: "Network down.", status: 0 };

async function openLift(label: string) {
  fireEvent.click(liftRow(label).getByRole("button", { name: "Update" }));
}

describe("one key per logical submission in Settings (P4-3)", () => {
  it("keeps an unanswered save for Retry, which resends the same body and key", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    submitStrengthEvidence.mockRejectedValueOnce(uncertain).mockRejectedValueOnce(uncertain)
      .mockResolvedValue(SQUAT_OK);
    render(<SettingsScreen />);

    await openSquat("140 kg");
    await saveSquat("150");
    await screen.findByText(UNCONFIRMED);
    // Save is not offered while the attempt is unresolved: the athlete retries or discards.
    expect(liftRow("Squat").queryByRole("button", { name: "Save" })).toBeNull();
    fireEvent.click(liftRow("Squat").getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(2));
    expect(submitStrengthEvidence.mock.calls[1][0]).toEqual(submitStrengthEvidence.mock.calls[0][0]);

    fireEvent.click(await liftRow("Squat").findByRole("button", { name: "Retry" }));
    await liftRow("Squat").findByRole("status");
    expect(keyOf(2)).toBe(keyOf(0));
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
  });

  it("keeps an uncertain squat across a successful bench save, and retries the original squat key", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140, bench_1rm_kg: 100 }));
    submitStrengthEvidence
      .mockRejectedValueOnce(uncertain)
      .mockResolvedValueOnce(recorded("pl_e1rm_bench", 105, 42))
      .mockResolvedValueOnce(SQUAT_OK);
    render(<SettingsScreen />);

    await openSquat("140 kg");
    await saveSquat("150");
    await screen.findByText(UNCONFIRMED);
    fireEvent.click(liftRow("Squat").getByRole("button", { name: "Cancel" }));

    await openLift("Bench press");
    fireEvent.change(screen.getByLabelText("Bench press: Weight in kg"), { target: { value: "105" } });
    fireEvent.click(liftRow("Bench press").getByRole("button", { name: "Save" }));
    await liftRow("Bench press").findByText("Saved. 1RM 105 kg recorded.");

    // The squat attempt survived the bench save and Cancel.
    liftRow("Squat").getByText(UNCONFIRMED);
    fireEvent.click(liftRow("Squat").getByRole("button", { name: "Retry" }));
    await liftRow("Squat").findByText("Saved. 1RM 150 kg recorded.");
    expect(submitStrengthEvidence.mock.calls[2][0]).toEqual(submitStrengthEvidence.mock.calls[0][0]);
    expect(keyOf(1)).not.toBe(keyOf(0));
  });

  it("keeps the original attempt when its Retry is refused, and a later Retry resends the original key", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    submitStrengthEvidence
      .mockRejectedValueOnce(uncertain) // committed, reply lost
      .mockRejectedValueOnce({ message: "Too many requests.", status: 429 })
      .mockResolvedValue(SQUAT_OK);
    render(<SettingsScreen />);

    await openSquat("140 kg");
    await saveSquat("150");
    await screen.findByText(UNCONFIRMED);
    fireEvent.click(liftRow("Squat").getByRole("button", { name: "Retry" }));
    await liftRow("Squat").findByText("Too many requests.");
    liftRow("Squat").getByText(UNCONFIRMED);
    expect(liftRow("Squat").queryByRole("button", { name: "Save" })).toBeNull();

    fireEvent.click(liftRow("Squat").getByRole("button", { name: "Retry" }));
    await liftRow("Squat").findByRole("status");
    expect([keyOf(1), keyOf(2)]).toEqual([keyOf(0), keyOf(0)]);
  });

  it("keeps a squat save in flight while bench saves: the squat cannot be sent again until it settles", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140, bench_1rm_kg: 100 }));
    let releaseSquat: (v: StrengthOutcome) => void = () => {};
    submitStrengthEvidence
      .mockReturnValueOnce(new Promise<StrengthOutcome>((r) => { releaseSquat = r; }))
      .mockResolvedValueOnce(recorded("pl_e1rm_bench", 105, 42));
    render(<SettingsScreen />);

    await openSquat("140 kg");
    await saveSquat("150");
    await openLift("Bench press");
    fireEvent.change(screen.getByLabelText("Bench press: Weight in kg"), { target: { value: "105" } });
    fireEvent.click(liftRow("Bench press").getByRole("button", { name: "Save" }));
    await liftRow("Bench press").findByText("Saved. 1RM 105 kg recorded.");

    // The squat is still in flight: reopened, its Save is disabled and sends nothing.
    await openLift("Squat");
    const squatSave = liftRow("Squat").getByRole("button", { name: "Saving…" });
    expect(squatSave).toHaveProperty("disabled", true);
    fireEvent.click(squatSave);
    expect(submitStrengthEvidence).toHaveBeenCalledTimes(2);

    releaseSquat(SQUAT_OK);
    await liftRow("Squat").findByText("Saved. 1RM 150 kg recorded.");
    expect(submitStrengthEvidence).toHaveBeenCalledTimes(2);
  });

  it("shows the unresolved attempt again when the editor is reopened after Cancel", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    submitStrengthEvidence.mockRejectedValueOnce(uncertain);
    render(<SettingsScreen />);

    await openSquat("140 kg");
    await saveSquat("150");
    await screen.findByText(UNCONFIRMED);
    fireEvent.click(liftRow("Squat").getByRole("button", { name: "Cancel" }));
    await openLift("Squat");
    liftRow("Squat").getByText(UNCONFIRMED);
    expect(liftRow("Squat").queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("Discard then Save records the same facts again under a new key", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    submitStrengthEvidence.mockRejectedValueOnce(uncertain).mockResolvedValue(SQUAT_OK);
    render(<SettingsScreen />);

    await openSquat("140 kg");
    await saveSquat("150");
    await screen.findByText(UNCONFIRMED);
    fireEvent.click(liftRow("Squat").getByRole("button", { name: "Discard" }));
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    await saveSquat("150");
    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(2));
    const { idempotency_key: first, ...firstFacts } = submitStrengthEvidence.mock.calls[0][0];
    const { idempotency_key: second, ...secondFacts } = submitStrengthEvidence.mock.calls[1][0];
    expect(secondFacts).toEqual(firstFacts);
    expect(second).not.toBe(first);
  });

  it("drops the attempt on a refusal, so the next Save is a new key", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    submitStrengthEvidence.mockRejectedValueOnce({ message: "Report refused.", status: 422 })
      .mockResolvedValue(SQUAT_OK);
    render(<SettingsScreen />);

    await openSquat("140 kg");
    await saveSquat("150");
    await screen.findByText("Report refused.");
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    await saveSquat("150");
    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(2));
    expect(keyOf(1)).not.toBe(keyOf(0));
  });

  it("mints a new key for the same report sent again after a success", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 150 }));
    submitStrengthEvidence.mockResolvedValue(SQUAT_OK);
    render(<SettingsScreen />);

    await openSquat("150 kg");
    await saveSquat("150");
    await waitFor(() => expect(screen.queryByLabelText("Squat: Weight in kg")).toBeNull());
    await openSquat("150 kg");
    await saveSquat("150");
    await waitFor(() => expect(submitStrengthEvidence).toHaveBeenCalledTimes(2));
    expect(keyOf(1)).not.toBe(keyOf(0));
  });
});

describe("the save acknowledgement in Settings (P4-3)", () => {
  const kept: UnderivedStrengthReportRead = {
    id: 8, benchmark_code: "pl_e1rm_squat", method: "rep_set", collection_mode: "retest",
    performed_at: null, load_kg: 160, reps: 5, rpe: null, rir: null, reason: "effort_unknown",
    created_at: "2026-10-12T10:00:00", superseded_by_observation_id: null, superseded_at: null,
  };

  it("shows the returned outcome even when the reports read after the save fails", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    listStrengthReports.mockResolvedValueOnce([]).mockRejectedValue({ message: "boom", status: 500 });
    submitStrengthEvidence.mockResolvedValue({
      outcome: "report_only", reason: "effort_unknown", value_available: false,
      observation: null, report: kept,
    } satisfies StrengthOutcome);
    render(<SettingsScreen />);

    await openSquat("140 kg");
    fireEvent.click(screen.getByRole("radio", { name: "A set I did" }));
    fireEvent.change(screen.getByLabelText("Squat: Load in kg"), { target: { value: "160" } });
    fireEvent.change(screen.getByLabelText("Squat: Reps"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(listStrengthReports).toHaveBeenCalledTimes(2));
    const ack = await liftRow("Squat").findByRole("status");
    expect(ack.textContent).toBe("160 kg × 5 recorded; 1RM estimate unavailable because effort is unknown.");
    squatRow().getByText("140 kg");
  });

  it("acknowledges a recorded value from the outcome, before the list is read", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    let releaseList: (v: UnderivedStrengthReportRead[]) => void = () => {};
    listStrengthReports.mockResolvedValueOnce([])
      .mockReturnValue(new Promise((r) => { releaseList = r; }));
    submitStrengthEvidence.mockResolvedValue(SQUAT_OK);
    render(<SettingsScreen />);

    await openSquat("140 kg");
    await saveSquat("150");
    expect((await liftRow("Squat").findByRole("status")).textContent).toBe("Saved. 1RM 150 kg recorded.");
    releaseList([]);
  });
});

describe("a report kept without an estimate, in Settings (P4-3)", () => {
  const report = (over: Record<string, unknown> = {}) => ({
    id: 3, benchmark_code: "pl_e1rm_squat", method: "rep_set", collection_mode: "retest",
    performed_at: "2026-10-06T04:00:00", load_kg: 160, reps: 5, rpe: null, rir: null,
    reason: "effort_unknown", created_at: "2026-10-06T10:00:00",
    superseded_by_observation_id: null, superseded_at: null, ...over,
  });

  it("shows the latest standing report on its own dated line and keeps the value", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    listStrengthReports.mockResolvedValue([
      report(),
      report({ id: 2, benchmark_code: "pl_e1rm_bench", reason: "effort_conflict", rpe: 8, rir: 0 }),
    ]);
    render(<SettingsScreen />);

    await waitFor(() => squatRow().getByText("140 kg"));
    const line = await screen.findByText(/160 kg × 5 recorded; 1RM estimate unavailable because effort is unknown\./);
    expect(line.textContent).toContain(new Date("2026-10-06T04:00:00Z").toLocaleDateString());
    screen.getByText(/RPE and RIR disagree\. This set is recorded, but no 1RM estimate was derived\./);
    expect(listStrengthReports).toHaveBeenCalledWith(TOKEN);
  });

  it("never replaces a missing value with the report, and hides a superseded report", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: null, bench_1rm_kg: 100 }));
    listStrengthReports.mockResolvedValue([
      report(),
      report({ id: 4, benchmark_code: "pl_e1rm_bench", superseded_by_observation_id: 9, superseded_at: "2026-10-07T00:00:00" }),
    ]);
    render(<SettingsScreen />);

    await screen.findByText(/160 kg × 5 recorded/);
    squatRow().getByText("—");
    expect(screen.getAllByText(/recorded; 1RM estimate unavailable/)).toHaveLength(1);
  });

  it("reads the reports again after a save", async () => {
    getProfile.mockResolvedValue(profile({ squat_1rm_kg: 140 }));
    submitStrengthEvidence.mockResolvedValue(SQUAT_OK);
    render(<SettingsScreen />);
    await openSquat("140 kg");
    await saveSquat("150");
    await waitFor(() => expect(listStrengthReports).toHaveBeenCalledTimes(2));
  });
});
