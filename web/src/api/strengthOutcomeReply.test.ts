// What the client does with the reply to a strength save (P4-3).
//
// A save resolves only on a reply that IS a StrengthOutcome. Anything else that arrives with a
// 2xx — a non-JSON body, JSON of another shape — confirms nothing: it rejects with no `status`,
// so the keyed caller keeps its attempt uncertain for Retry instead of dropping the key.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StrengthEvidenceCreate, StrengthOutcome } from "@/types";
import { createSubmissionAttempts, isUncertainFailure } from "../perflab/screens/useSubmissionKey";

type Client = typeof import("./perfLabClient");

const BASE = "http://api.test";
const BODY: StrengthEvidenceCreate = {
  benchmark_code: "pl_e1rm_squat", method: "tested_max", value_kg: 150, collection_mode: "retest",
  idempotency_key: "8d0c7b1e-2f43-4a77-9d43-3f1b8e0c6a10",
};

const RECORDED: StrengthOutcome = {
  outcome: "recorded", reason: null, value_available: true, report: null,
  observation: {
    id: 41, user_id: 7, benchmark_definition_id: 3, benchmark_code: "pl_e1rm_squat",
    observed_at: "2026-10-12T10:00:00", raw_value: 150, secondary_value: null, normalized_value: null,
    validity_status: "valid", source: "manual", state_disposition: "applied", state_disposition_reason: null,
  },
};

const REPORT_ONLY: StrengthOutcome = {
  outcome: "report_only", reason: "effort_unknown", value_available: false, observation: null,
  report: {
    id: 9, benchmark_code: "pl_e1rm_squat", method: "rep_set", collection_mode: "retest",
    performed_at: "2026-10-11T10:00:00", load_kg: 100, reps: 5, rpe: null, rir: null,
    reason: "effort_unknown", created_at: "2026-10-12T10:00:00",
    superseded_by_observation_id: null, superseded_at: null,
  },
};

async function loadClient(): Promise<Client> {
  vi.stubEnv("VITE_API_BASE_URL", BASE);
  vi.resetModules();
  return import("./perfLabClient");
}

function reply(status: number, body: string | null, type = "application/json"): Response {
  return new Response(body, { status, headers: { "content-type": type } });
}

/** 200 replies whose nested record breaks the server's read schema (app/schemas/benchmarks.py
 *  UnderivedStrengthReportRead / BenchmarkObservationRead), which the server never sends. */
const MALFORMED_RECORDS: ReadonlyArray<readonly [string, unknown]> = [
  ["a report with fractional reps", { ...REPORT_ONLY, report: { ...REPORT_ONLY.report, reps: 5.5 } }],
  ["a report with a fractional id", { ...REPORT_ONLY, report: { ...REPORT_ONLY.report, id: 9.5 } }],
  ["a report whose method is not rep_set", { ...REPORT_ONLY, report: { ...REPORT_ONLY.report, method: "tested_max" } }],
  ["a report with an unknown reason", { ...REPORT_ONLY, report: { ...REPORT_ONLY.report, reason: "effort_low" } }],
  ["a report with a fractional superseding id", { ...REPORT_ONLY, report: { ...REPORT_ONLY.report, superseded_by_observation_id: 3.5 } }],
  ["a report with an unreadable created_at", { ...REPORT_ONLY, report: { ...REPORT_ONLY.report, created_at: "yesterday-ish" } }],
  ["a report missing load_kg", { ...REPORT_ONLY, report: { ...REPORT_ONLY.report, load_kg: undefined } }],
  ["an observation with a fractional id", { ...RECORDED, observation: { ...RECORDED.observation, id: 41.5 } }],
  ["an observation with a string user_id", { ...RECORDED, observation: { ...RECORDED.observation, user_id: "7" } }],
  ["an observation missing observed_at", { ...RECORDED, observation: { ...RECORDED.observation, observed_at: undefined } }],
  ["an observation with an unknown disposition", { ...RECORDED, observation: { ...RECORDED.observation, state_disposition: "maybe" } }],
];

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("submitStrengthEvidence", () => {
  it("resolves a StrengthOutcome, recorded or report-only", async () => {
    const { submitStrengthEvidence } = await loadClient();
    fetchMock.mockResolvedValueOnce(reply(200, JSON.stringify(RECORDED)));
    await expect(submitStrengthEvidence(BODY, "tok")).resolves.toEqual(RECORDED);
    fetchMock.mockResolvedValueOnce(reply(200, JSON.stringify(REPORT_ONLY)));
    await expect(submitStrengthEvidence(BODY, "tok")).resolves.toEqual(REPORT_ONLY);
  });

  it.each<readonly [string, Response]>([
    ["a non-JSON 200", reply(200, "OK", "text/plain")],
    ["an empty JSON 200", reply(200, null)],
    ["JSON of another shape", reply(200, JSON.stringify({ id: 41, raw_value: 150 }))],
    ["an outcome with both records", reply(200, JSON.stringify({ ...RECORDED, report: REPORT_ONLY.report }))],
    ["a recorded outcome with no value", reply(200, JSON.stringify({ ...RECORDED, value_available: false }))],
    ["a report-only outcome with no reason", reply(200, JSON.stringify({ ...REPORT_ONLY, reason: null }))],
    ...MALFORMED_RECORDS.map(([name, body]) => [name, reply(200, JSON.stringify(body))] as const),
  ])("rejects %s as uncertain, never as a confirmed save", async (_name, response) => {
    const { submitStrengthEvidence } = await loadClient();
    fetchMock.mockResolvedValueOnce(response);
    const error = await submitStrengthEvidence(BODY, "tok").then(
      () => { throw new Error("resolved"); },
      (e: unknown) => e,
    );
    expect(isUncertainFailure(error)).toBe(true);
  });
});

describe("a malformed 200 through the attempt helper", () => {
  it.each(MALFORMED_RECORDS)("keeps the original key after %s, and Retry resends it", async (_name, body) => {
    const { submitStrengthEvidence } = await loadClient();
    const attempts = createSubmissionAttempts(() => "key-1");
    const facts: StrengthEvidenceCreate = { ...BODY };
    delete facts.idempotency_key;
    fetchMock
      .mockResolvedValueOnce(reply(200, JSON.stringify(body)))
      .mockResolvedValueOnce(reply(200, JSON.stringify(REPORT_ONLY)));
    const submit = (b: StrengthEvidenceCreate) => submitStrengthEvidence(b, "tok");

    await expect(attempts.save("squat", facts, submit)).rejects.toBeTruthy();
    expect(attempts.unresolved("squat")?.idempotency_key).toBe("key-1");

    await expect(attempts.retry("squat", submit)).resolves.toEqual(REPORT_ONLY);
    const sent = fetchMock.mock.calls.map(([, init]) => JSON.parse((init as RequestInit).body as string));
    expect(sent.map((b) => b.idempotency_key)).toEqual(["key-1", "key-1"]);
    expect(sent[1]).toEqual(sent[0]);
    expect(attempts.unresolved("squat")).toBeUndefined();
  });
});
