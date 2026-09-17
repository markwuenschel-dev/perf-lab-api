// What the client does with a body-less HTTP reply.
//
// The defect this file pins: FastAPI builds a 204 through its default JSON response class, so
// the reply keeps `content-type: application/json` while the body is empty. The client decided
// "is this JSON?" from that header alone and called res.json() on nothing, which throws
// "Unexpected end of JSON input" — surfaced to the athlete as a JSON error on every DELETE
// (objective, block, Oura disconnect) even though the row was already deleted.
//
// The invariant, stated once: 204 IS THE ONLY EMPTY SUCCESS BODY THIS CLIENT ACCEPTS.
// An empty or malformed 200 where a payload is expected stays a contract failure — silently
// resolving those to undefined would turn a broken endpoint into a "successful" no-op.
//
// The other half of the seam — that the route really answers 204 with an empty body — is proven
// server-side in tests/test_delete_routes_no_content.py.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Client = typeof import("./perfLabClient");

const BASE = "http://api.test";

/** perfLabClient reads VITE_API_BASE_URL at module scope, so stub the env before importing it. */
async function loadClient(): Promise<Client> {
  vi.stubEnv("VITE_API_BASE_URL", BASE);
  vi.resetModules();
  return import("./perfLabClient");
}

/** A real Response, not a hand-rolled stub: only the genuine body/header pairing reproduces this. */
function jsonLabelled(status: number, body: string | null): Response {
  return new Response(body, {
    status,
    headers: { "content-type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("204 No Content — the deletion path", () => {
  it("resolves instead of throwing when the empty body is labelled application/json", async () => {
    const { deleteObjective } = await loadClient();
    // Exactly what FastAPI returns: 204, JSON content-type, no body.
    fetchMock.mockResolvedValue(jsonLabelled(204, null));

    await expect(deleteObjective(7, "tok")).resolves.toBeUndefined();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE}/v1/objectives/7`);
    expect((init as RequestInit).method).toBe("DELETE");
  });

  it("accepts a 204 with no content-type at all", async () => {
    const { deleteObjective } = await loadClient();
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await expect(deleteObjective(7, "tok")).resolves.toBeUndefined();
  });

  it("covers the other two routes that answer 204 — macrocycle delete and Oura disconnect", async () => {
    const { deleteMacrocycle, disconnectOura } = await loadClient();
    fetchMock.mockResolvedValue(jsonLabelled(204, null));

    await expect(deleteMacrocycle(3, "tok")).resolves.toBeUndefined();
    await expect(disconnectOura("tok")).resolves.toBeUndefined();
  });
});

describe("what must still fail", () => {
  it("keeps an empty 200 a contract failure where a payload is expected", async () => {
    const { listObjectives } = await loadClient();
    fetchMock.mockResolvedValue(jsonLabelled(200, ""));

    // The narrow fix is keyed on status 204, so this must NOT become undefined.
    await expect(listObjectives("tok")).rejects.toBeDefined();
  });

  it("keeps a malformed 200 a failure", async () => {
    const { listObjectives } = await loadClient();
    fetchMock.mockResolvedValue(jsonLabelled(200, "{not json"));

    await expect(listObjectives("tok")).rejects.toBeDefined();
  });
});

describe("error handling is unchanged by the fix", () => {
  it("still reports an error status whose body is empty", async () => {
    const { deleteObjective } = await loadClient();
    fetchMock.mockResolvedValue(new Response(null, { status: 500, statusText: "Server Error" }));

    await expect(deleteObjective(7, "tok")).rejects.toMatchObject({
      status: 500,
      message: "Server Error",
    });
  });

  it("still reports an error status whose JSON body is malformed", async () => {
    const { deleteObjective } = await loadClient();
    fetchMock.mockResolvedValue(jsonLabelled(404, "{not json"));

    await expect(deleteObjective(7, "tok")).rejects.toMatchObject({ status: 404 });
  });

  it("still flattens a FastAPI detail string", async () => {
    const { deleteObjective } = await loadClient();
    fetchMock.mockResolvedValue(jsonLabelled(404, JSON.stringify({ detail: "Objective not found" })));

    await expect(deleteObjective(7, "tok")).rejects.toMatchObject({
      status: 404,
      message: "Objective not found",
    });
  });

  it("still signs out on a 401 — the sessionOn401 bridge is untouched", async () => {
    const { deleteObjective } = await loadClient();
    const { setUnauthorizedHandler } = await import("../auth/sessionBridge");
    const cleared = vi.fn();
    setUnauthorizedHandler(cleared);
    fetchMock.mockResolvedValue(jsonLabelled(401, JSON.stringify({ detail: "Not authenticated" })));

    await expect(deleteObjective(7, "tok")).rejects.toMatchObject({ status: 401 });
    expect(cleared).toHaveBeenCalledTimes(1);
    setUnauthorizedHandler(null);
  });
});
