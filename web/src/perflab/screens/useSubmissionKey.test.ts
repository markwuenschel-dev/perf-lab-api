// src/perflab/screens/useSubmissionKey.test.ts
//
// The attempt logic behind one idempotency key per logical strength submission (P4-3): Save is
// always a new key; only Retry resends one, unchanged; attempts are kept per slot, so resolving
// one lift never loses another's unresolved attempt. An uncertain attempt is kept until a
// confirmed outcome or Discard — a refused retry does not resolve it — and a slot holds one
// attempt at a time, so an older reply can never settle a newer attempt.
import { describe, expect, it } from "vitest";
import type { StrengthEvidenceCreate } from "@/types";
import { createSubmissionAttempts, isUncertainFailure, type SubmissionAttempt } from "./useSubmissionKey";

const squat: StrengthEvidenceCreate = {
  benchmark_code: "pl_e1rm_squat", method: "rep_set", load_kg: 100, reps: 5, collection_mode: "retest",
};
const bench: StrengthEvidenceCreate = {
  benchmark_code: "pl_e1rm_bench", method: "tested_max", value_kg: 90, collection_mode: "retest",
};

function harness() {
  let n = 0;
  const attempts = createSubmissionAttempts(() => `key-${++n}`);
  const sent: string[] = [];
  const answer = (outcome: "ok" | { status?: number }) => (body: SubmissionAttempt) => {
    sent.push(JSON.stringify(body));
    return outcome === "ok" ? Promise.resolve("ok") : Promise.reject({ message: "x", ...outcome });
  };
  return { attempts, sent, answer };
}

describe("isUncertainFailure", () => {
  it("treats only a received 4xx as certain", () => {
    expect(isUncertainFailure({ message: "refused", status: 422 })).toBe(false);
    expect(isUncertainFailure({ message: "conflict", status: 409 })).toBe(false);
    expect(isUncertainFailure({ message: "down", status: 503 })).toBe(true);
    expect(isUncertainFailure({ message: "down", status: 0 })).toBe(true);
    expect(isUncertainFailure(new TypeError("Failed to fetch"))).toBe(true);
    expect(isUncertainFailure(undefined)).toBe(true);
  });
});

describe("submission attempts per slot", () => {
  it("keeps an uncertain squat's key across a successful bench, and Retry resends it", async () => {
    const { attempts, sent, answer } = harness();
    await expect(attempts.save("squat", squat, answer({}))).rejects.toBeTruthy();
    await attempts.save("bench", bench, answer("ok"));
    await attempts.retry("squat", answer("ok"));

    expect(sent).toHaveLength(3);
    expect(sent[2]).toBe(sent[0]);
    expect(JSON.parse(sent[0]).idempotency_key).toBe("key-1");
    expect(JSON.parse(sent[1]).idempotency_key).toBe("key-2");
    expect(attempts.unresolved("squat")).toBeUndefined();
  });

  it("Save mints a new key even for identical facts; only Retry reuses one", async () => {
    const { attempts, sent, answer } = harness();
    await expect(attempts.save("squat", squat, answer({ status: 503 }))).rejects.toBeTruthy();
    await expect(attempts.retry("squat", answer({ status: 503 }))).rejects.toBeTruthy();
    expect(sent[1]).toBe(sent[0]);
    expect(attempts.unresolved("squat")?.idempotency_key).toBe("key-1");

    attempts.discard("squat");
    expect(attempts.unresolved("squat")).toBeUndefined();
    await attempts.save("squat", squat, answer("ok"));
    const { idempotency_key: first, ...firstFacts } = JSON.parse(sent[0]);
    const { idempotency_key: third, ...thirdFacts } = JSON.parse(sent[2]);
    expect(thirdFacts).toEqual(firstFacts);
    expect(third).not.toBe(first);
  });

  it("refuses Save on a slot whose attempt is uncertain, so its key is never replaced", async () => {
    const { attempts, sent, answer } = harness();
    await expect(attempts.save("squat", squat, answer({}))).rejects.toBeTruthy();
    await expect(attempts.save("squat", squat, answer("ok"))).rejects.toThrow(/unresolved/);
    expect(sent).toHaveLength(1);
    expect(attempts.unresolved("squat")?.idempotency_key).toBe("key-1");
  });

  it.each([401, 422, 429])(
    "keeps the original attempt when its Retry is refused with %i: the refusal proves nothing about the first send",
    async (status) => {
      const { attempts, sent, answer } = harness();
      await expect(attempts.save("squat", squat, answer({}))).rejects.toBeTruthy(); // committed, reply lost
      await expect(attempts.retry("squat", answer({ status }))).rejects.toBeTruthy();
      expect(attempts.unresolved("squat")?.idempotency_key).toBe("key-1");
      await expect(attempts.save("squat", squat, answer("ok"))).rejects.toThrow(/unresolved/);
      await attempts.retry("squat", answer("ok"));
      expect(sent.map((s) => JSON.parse(s).idempotency_key)).toEqual(["key-1", "key-1", "key-1"]);
      expect(attempts.unresolved("squat")).toBeUndefined();
    },
  );

  it("never lets an older reply settle a newer attempt: one send per slot at a time", async () => {
    const { attempts, sent, answer } = harness();
    let release: (v: string) => void = () => {};
    const first = attempts.save("squat", squat, (body) => {
      sent.push(JSON.stringify(body));
      return new Promise<string>((r) => { release = r; });
    });
    expect(attempts.inFlight("squat")).toBe(true);
    // A second Save (or a Retry, or a Discard) while key-1 is in flight starts nothing.
    await expect(attempts.save("squat", squat, answer({}))).rejects.toThrow(/in flight/);
    await expect(attempts.retry("squat", answer("ok"))).rejects.toThrow(/nothing to retry/);
    attempts.discard("squat");
    expect(attempts.inFlight("squat")).toBe(true);
    expect(sent).toHaveLength(1);
    // Another slot is independent.
    await attempts.save("bench", bench, answer("ok"));
    expect(attempts.inFlight("squat")).toBe(true);

    release("ok");
    await first;
    expect(attempts.inFlight("squat")).toBe(false);
    expect(attempts.unresolved("squat")).toBeUndefined();
  });

  it("an attempt discarded while uncertain is not revived by anything; the next Save is a new key", async () => {
    const { attempts, sent, answer } = harness();
    await expect(attempts.save("squat", squat, answer({ status: 503 }))).rejects.toBeTruthy();
    attempts.discard("squat");
    await expect(attempts.retry("squat", answer("ok"))).rejects.toThrow(/nothing to retry/);
    await expect(attempts.save("squat", squat, answer({}))).rejects.toBeTruthy();
    expect(JSON.parse(sent[1]).idempotency_key).toBe("key-2");
    expect(attempts.unresolved("squat")?.idempotency_key).toBe("key-2");
  });

  it("keeps the attempt when the reply was not a confirmed outcome (no status)", async () => {
    const { attempts, answer } = harness();
    await expect(attempts.save("squat", squat, answer({}))).rejects.toBeTruthy();
    expect(attempts.unresolved("squat")?.idempotency_key).toBe("key-1");
  });

  it("drops the attempt on a 4xx refusal of its first send, so the next Save is a new key", async () => {
    const { attempts, sent, answer } = harness();
    await expect(attempts.save("squat", squat, answer({ status: 422 }))).rejects.toBeTruthy();
    expect(attempts.unresolved("squat")).toBeUndefined();
    await expect(attempts.retry("squat", answer("ok"))).rejects.toThrow(/nothing to retry/);
    await attempts.save("squat", squat, answer("ok"));
    expect(JSON.parse(sent[1]).idempotency_key).toBe("key-2");
  });

  it("is in flight, not unresolved, during its first send, and ignores a key in the given body", async () => {
    const { attempts } = harness();
    let release: (v: string) => void = () => {};
    const inFlight = attempts.save("squat", { ...squat, idempotency_key: "client-sent" }, (body) => {
      expect(body.idempotency_key).toBe("key-1");
      return new Promise<string>((r) => { release = r; });
    });
    expect(attempts.unresolved("squat")).toBeUndefined();
    release("ok");
    await inFlight;
  });
});
