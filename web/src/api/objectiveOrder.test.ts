// The wire shape of the display-order write (ADR-0061): PUT /v1/objectives/order with the
// full active id list and nothing else — never a `priority` field, never a PATCH on an
// objective. Display order is not a weight; a priority write here would silently change
// what drives prescription.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Client = typeof import("./perfLabClient");

const BASE = "http://api.test";

async function loadClient(): Promise<Client> {
  vi.stubEnv("VITE_API_BASE_URL", BASE);
  vi.resetModules();
  return import("./perfLabClient");
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(() => Promise.resolve(new Response("[]", { status: 200, headers: { "content-type": "application/json" } })));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("setObjectiveOrder", () => {
  it("PUTs exactly { objective_ids } to /v1/objectives/order", async () => {
    const { setObjectiveOrder } = await loadClient();
    await setObjectiveOrder([13, 11, 12], "tok");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${BASE}/v1/objectives/order`);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ objective_ids: [13, 11, 12] });
  });
});

describe("getDrivingObjective", () => {
  it("GETs /v1/objectives/driving", async () => {
    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(new Response(JSON.stringify({ objective_id: 7, source: "priority" }), { status: 200, headers: { "content-type": "application/json" } })),
    );
    const { getDrivingObjective } = await loadClient();
    expect(await getDrivingObjective("tok")).toEqual({ objective_id: 7, source: "priority" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${BASE}/v1/objectives/driving`);
    expect(init.method).toBeUndefined();
  });
});
