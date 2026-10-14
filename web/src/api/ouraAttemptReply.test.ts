// What the client does with an Oura /complete or /cancel reply, and what each reply means for
// the attempt the tab holds (P6b review, 2026-10-14).
//
// Only a reply the tab recognizes as terminal lets it forget the attempt's inputs. A 200 that is
// not one of the server's shapes (a success without its receipt, a failure without a known reason)
// rejects, so the caller treats it as uncertain and keeps the inputs for Retry. An unknown status
// code is uncertain too.
import { afterEach, describe, expect, it, vi } from "vitest";
import { outcomeOf, readAttemptResult } from "./ouraReply";

type Client = typeof import("./perfLabClient");

const SUCCEEDED = {
  status: "succeeded", reason: null, completed_at: "2026-10-13T10:00:00",
  linked: { connection_id: 7, generation: 2 },
};

async function clientReplying(status: number, body: unknown): Promise<Client> {
  vi.stubEnv("VITE_API_BASE_URL", "http://api.test");
  vi.resetModules();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json" },
  })));
  return import("./perfLabClient");
}

function call(client: Client, op: "complete" | "cancel") {
  return op === "complete"
    ? client.completeOuraAuthorization({ state: "S1", code: "C1", code_verifier: "v".repeat(64) }, "t1")
    : client.cancelOuraAuthorization({ state: "S1", code_verifier: "v".repeat(64) }, "t1");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("a 200 that is not an outcome rejects", () => {
  it.each([
    ["complete", "success without its receipt", { status: "succeeded" }],
    ["cancel", "failure without a reason", { status: "failed", reason: null }],
    ["complete", "failure with an unknown reason", { status: "failed", reason: "mystery" }],
    ["cancel", "success with a partial receipt", { ...SUCCEEDED, linked: { connection_id: 7 } }],
    ["complete", "an unknown status", { status: "done" }],
    ["cancel", "not an object", ["succeeded"]],
    // Review, 2026-10-14 (second round): every field of an outcome, not just its status.
    ["complete", "success with an unreadable timestamp", { ...SUCCEEDED, completed_at: "not-a-time" }],
    ["complete", "success with an impossible date", { ...SUCCEEDED, completed_at: "2026-13-45T10:00:00" }],
    // Third round: Date.parse rolls these over into real dates; the calendar must refuse them.
    ["complete", "success on February 30", { ...SUCCEEDED, completed_at: "2026-02-30T10:00:00" }],
    ["complete", "success on February 29 of a common year", { ...SUCCEEDED, completed_at: "2026-02-29T10:00:00" }],
    ["complete", "success on February 29, 1900", { ...SUCCEEDED, completed_at: "1900-02-29T10:00:00" }],
    ["complete", "success on April 31", { ...SUCCEEDED, completed_at: "2026-04-31T10:00:00" }],
    ["complete", "success on day 0", { ...SUCCEEDED, completed_at: "2026-10-00T10:00:00" }],
    ["complete", "success at hour 24", { ...SUCCEEDED, completed_at: "2026-10-13T24:00:00" }],
    ["complete", "success at second 60", { ...SUCCEEDED, completed_at: "2026-10-13T10:00:60" }],
    ["cancel", "failure with an impossible offset",
      { status: "failed", reason: "cancelled", completed_at: "2026-10-13T10:00:00+24:00", linked: null }],
    ["cancel", "failure with an object as its time",
      { status: "failed", reason: "cancelled", completed_at: { not: "a timestamp" }, linked: null }],
    ["cancel", "failure carrying a receipt",
      { status: "failed", reason: "cancelled", completed_at: "2026-10-13T10:00:00", linked: SUCCEEDED.linked }],
    ["complete", "in progress with a time", { status: "in_progress", reason: null, completed_at: "2026-10-13T10:00:00" }],
    ["cancel", "in progress with a receipt", { status: "in_progress", reason: null, linked: SUCCEEDED.linked }],
  ])("%s: %s", async (op, _name, body) => {
    const client = await clientReplying(200, body);
    await expect(call(client, op as "complete" | "cancel")).rejects.toMatchObject({ status: 200 });
  });

  it.each([
    ["complete", SUCCEEDED],
    ["complete", { ...SUCCEEDED, completed_at: "2026-10-13T10:00:00.123456" }],
    ["complete", { ...SUCCEEDED, completed_at: "2026-10-13T10:00:00Z" }],
    ["complete", { ...SUCCEEDED, completed_at: "2028-02-29T10:00:00" }],
    ["complete", { ...SUCCEEDED, completed_at: "2000-02-29T23:59:59+05:30" }],
    ["complete", { ...SUCCEEDED, completed_at: "2026-12-31T00:00:00-08:00" }],
    ["cancel", { status: "failed", reason: "cancelled", completed_at: "2026-10-13T10:00:00", linked: null }],
    ["cancel", { status: "in_progress", reason: null, completed_at: null, linked: null }],
  ])("%s resolves a reply the server does send", async (op, body) => {
    const client = await clientReplying(200, body);
    await expect(call(client, op as "complete" | "cancel")).resolves.toEqual(body);
  });
});

describe("what a reply means for the stored attempt", () => {
  const error = (status: number, detail?: unknown) => ({
    message: `HTTP ${status}`, status, details: detail === undefined ? undefined : { detail },
  });

  it.each([
    ["a receipt", SUCCEEDED, null, "succeeded"],
    ["409 failed with a known reason", null, error(409, { status: "failed", reason: "expired" }), "failed"],
    ["400 invalid_state", null, error(400, { status: "invalid", reason: "invalid_state" }), "refused"],
    ["400 verifier_mismatch", null, error(400, { status: "invalid", reason: "verifier_mismatch" }), "refused"],
    ["401", null, error(401), "signIn"],
    ["403", null, error(403, { status: "forbidden", reason: "not_your_attempt" }), "wrongAccount"],
    ["409 in progress", null, error(409, { status: "in_progress", reason: null }), "uncertain"],
    ["409 failed with an unknown reason", null, error(409, { status: "failed", reason: "mystery" }), "uncertain"],
    ["409 failed without a reason", null, error(409, { status: "failed", reason: null }), "uncertain"],
    ["429", null, error(429), "uncertain"],
    ["400 without a recognized detail", null, error(400, "Bad Request"), "uncertain"],
    ["422", null, error(422, [{ msg: "field required" }]), "uncertain"],
    ["503 outcome_unknown", null, error(503, { status: "unknown", reason: "outcome_unknown" }), "uncertain"],
    ["a network failure", null, new TypeError("Failed to fetch"), "uncertain"],
    ["a malformed result", { status: "succeeded" }, null, "uncertain"],
  ])("%s → %s", (_name, result, err, kind) => {
    expect(outcomeOf(result as never, err).kind).toBe(kind);
  });

  it("only a failure with a known reason is a failure", () => {
    expect(readAttemptResult({ status: "failed", reason: "claim_stale" })).not.toBeNull();
    expect(readAttemptResult({ status: "failed", reason: "" })).toBeNull();
  });
});
