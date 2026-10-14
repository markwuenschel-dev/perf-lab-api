// @vitest-environment jsdom
//
// src/perflab/ouraPkce.test.ts: the browser half of a PKCE attempt (P6b, ADR-0075).
import { afterEach, describe, expect, it } from "vitest";
import {
  authorizeUrlCarries,
  challengeFor,
  clearAttemptIf,
  generateVerifier,
  inFlightCountForTests,
  loadAttempt,
  once,
  saveAttempt,
} from "./ouraPkce";
import { OURA_PKCE_STORAGE_KEY } from "./wearableSync";

const CH = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM";

afterEach(() => window.sessionStorage.clear());

describe("PKCE", () => {
  it("matches RFC 7636 appendix B", async () => {
    expect(await challengeFor("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe(CH);
  });

  it("makes 64-character unreserved verifiers, different every time", () => {
    const a = generateVerifier();
    expect(a).toMatch(/^[A-Za-z0-9\-._~]{64}$/);
    expect(generateVerifier()).not.toBe(a);
  });

  it("opens only a consent URL that carries our challenge with S256", () => {
    const ok = `https://cloud.ouraring.com/oauth/authorize?state=S1&code_challenge=${CH}&code_challenge_method=S256`;
    expect(authorizeUrlCarries(ok, CH)).toBe("S1");
    expect(authorizeUrlCarries("https://cloud.ouraring.com/oauth/authorize?state=S1", CH)).toBeNull();
    expect(authorizeUrlCarries(ok.replace(CH, "A".repeat(43)), CH)).toBeNull();
    expect(authorizeUrlCarries(ok.replace("S256", "plain"), CH)).toBeNull();
    expect(authorizeUrlCarries(`${ok}&code_challenge=${CH}`, CH)).toBeNull();
    expect(authorizeUrlCarries(ok.replace("state=S1&", ""), CH)).toBeNull();
    expect(authorizeUrlCarries("not a url", CH)).toBeNull();
  });
});

describe("the stored attempt", () => {
  const rec = { state: "A", verifier: "v", challenge: CH, startedAt: 1, userKey: "me@x" };

  it("round-trips and survives garbage", () => {
    saveAttempt(rec);
    expect(loadAttempt()).toEqual(rec);
    window.sessionStorage.setItem(OURA_PKCE_STORAGE_KEY, "{not json");
    expect(loadAttempt()).toBeNull();
    window.sessionStorage.setItem(OURA_PKCE_STORAGE_KEY, JSON.stringify({ state: 1 }));
    expect(loadAttempt()).toBeNull();
  });

  it("is cleared only by its own state and identity", () => {
    saveAttempt({ ...rec, state: "B" });
    clearAttemptIf("A", "me@x");
    expect(loadAttempt()?.state).toBe("B");
    clearAttemptIf("B", "someone-else@x");
    expect(loadAttempt()?.state).toBe("B");
    clearAttemptIf("B", "me@x");
    expect(loadAttempt()).toBeNull();
  });
});

describe("in-flight requests", () => {
  it("share an outstanding request per operation, identity and attempt, then forget it", async () => {
    let calls = 0;
    let release!: (v: string) => void;
    const run = () => {
      calls += 1;
      return new Promise<string>((r) => (release = r));
    };
    const a = once("complete", "me", "S", run);
    const b = once("complete", "me", "S", run);
    once("cancel", "me", "S", () => Promise.resolve("other op"));
    once("complete", "you", "S", () => Promise.resolve("other identity"));
    expect(calls).toBe(1);
    release("done");
    expect(await a).toBe("done");
    expect(await b).toBe("done");
    await Promise.resolve();
    expect(inFlightCountForTests()).toBe(0);
    await once("complete", "me", "S", () => Promise.resolve("again"));
    expect(calls).toBe(1); // the retry ran its own function, not the settled one
  });

  it("forgets a rejected request too, so Retry sends a new one", async () => {
    await expect(once("complete", "me", "R", () => Promise.reject(new TypeError("net")))).rejects.toThrow("net");
    expect(inFlightCountForTests()).toBe(0);
    expect(await once("complete", "me", "R", () => Promise.resolve("ok"))).toBe("ok");
  });
});
