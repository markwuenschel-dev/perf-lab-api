// @vitest-environment jsdom
//
// Answers belong to the session that asked, through the REAL client, bridge and <AuthProvider>
// (only `fetch` is stubbed, routed by path and bearer token):
//   - A 401 answering a request made with an earlier session's token does not sign out the
//     current session; a 401 for the current token still does.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const BASE = "http://api.test";
const TOKEN_A = "token-a";
const TOKEN_B = "token-b";
const USER_A = { id: 1, email: "a@example.com", is_active: true };
const USER_B = { id: 2, email: "b@example.com", is_active: true };
const PROFILE = { timezone: "Europe/London", timezone_source: "athlete" };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function deferred<T = void>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

type Route = (init: RequestInit | undefined) => Promise<Response> | Response;
/** Per-test routes: `me[token]`, `token[email]`, and `overview[token]`. */
let routes: {
  me: Record<string, Route>;
  token: Record<string, Route>;
  overview: Record<string, Route>;
};

function bearer(init: RequestInit | undefined): string {
  const h = (init?.headers ?? {}) as Record<string, string>;
  return (h.Authorization ?? "").replace(/^Bearer /, "");
}

async function router(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const path = new URL(String(input)).pathname;
  if (path === "/auth/token") {
    const email = new URLSearchParams(String(init?.body)).get("username") ?? "";
    return routes.token[email](init);
  }
  if (path === "/auth/me") return routes.me[bearer(init)](init);
  if (path === "/v1/profile") return json(200, PROFILE);
  if (path === "/v1/dashboard/overview") return routes.overview[bearer(init)](init);
  throw new Error(`unrouted ${path}`);
}

type Modules = {
  client: typeof import("../api/perfLabClient");
  AuthProvider: typeof import("./AuthContext").AuthProvider;
  useAuth: typeof import("./useAuth").useAuth;
};

/** The client reads VITE_API_BASE_URL at module scope, and the bridge is module state: load the
 *  client, the bridge and the provider together from one fresh module graph. */
async function load(): Promise<Modules> {
  vi.stubEnv("VITE_API_BASE_URL", BASE);
  vi.resetModules();
  const client = await import("../api/perfLabClient");
  const { AuthProvider } = await import("./AuthContext");
  const { useAuth } = await import("./useAuth");
  return { client, AuthProvider, useAuth };
}

let overviewResult: Promise<unknown> | null = null;

async function mount(storedToken: string | null, timeoutMs = 8000) {
  const { client, AuthProvider, useAuth } = await load();
  if (storedToken) sessionStorage.setItem("perf_lab_access_token", storedToken);
  function Probe() {
    const auth = useAuth();
    return (
      <div>
        <span data-testid="token">{auth.token ?? "none"}</span>
        <span data-testid="user">{auth.user?.email ?? "none"}</span>
        <span data-testid="loading">{String(auth.isLoading)}</span>
        <span data-testid="session">{auth.sessionUnavailable?.reason ?? "ok"}</span>
        <button onClick={() => void auth.login(USER_A.email, "pw").catch(() => undefined)}>login a</button>
        <button onClick={() => void auth.login(USER_B.email, "pw").catch(() => undefined)}>login b</button>
        <button onClick={() => auth.logout()}>logout</button>
        <button onClick={() => void auth.retrySession()}>retry</button>
        <button
          onClick={() => {
            // A screen's read, made with whatever token is current at the click.
            overviewResult = client.getDashboardOverview(auth.token ?? "").catch((e: unknown) => e);
          }}
        >
          read
        </button>
      </div>
    );
  }
  render(
    <AuthProvider profileTimeoutMs={timeoutMs}>
      <Probe />
    </AuthProvider>,
  );
}

const text = (id: string) => screen.getByTestId(id).textContent;

beforeEach(() => {
  routes = {
    me: { [TOKEN_A]: () => json(200, USER_A), [TOKEN_B]: () => json(200, USER_B) },
    token: {
      [USER_A.email]: () => json(200, { access_token: TOKEN_A, token_type: "bearer" }),
      [USER_B.email]: () => json(200, { access_token: TOKEN_B, token_type: "bearer" }),
    },
    overview: {},
  };
  vi.stubGlobal("fetch", vi.fn(router));
  overviewResult = null;
});

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("a 401 signs out only the session whose token it refused", () => {
  it("a late 401 for token A, after token B is stored, leaves B's session standing", async () => {
    const lateA = deferred();
    routes.overview[TOKEN_A] = () => lateA.promise.then(() => json(401, { detail: "Not authenticated" }));
    await mount(TOKEN_A);
    await waitFor(() => expect(text("user")).toBe(USER_A.email));
    fireEvent.click(screen.getByText("read")); // made with token A, answers later

    fireEvent.click(screen.getByText("login b"));
    await waitFor(() => expect(text("user")).toBe(USER_B.email));

    await act(async () => lateA.resolve());
    await expect(overviewResult).resolves.toMatchObject({ status: 401 });
    await act(async () => undefined);
    expect(text("token")).toBe(TOKEN_B);
    expect(text("user")).toBe(USER_B.email);
    expect(sessionStorage.getItem("perf_lab_access_token")).toBe(TOKEN_B);
  });

  it("a 401 for the current token still signs out", async () => {
    routes.overview[TOKEN_A] = () => json(401, { detail: "Not authenticated" });
    await mount(TOKEN_A);
    await waitFor(() => expect(text("user")).toBe(USER_A.email));
    fireEvent.click(screen.getByText("read"));
    await waitFor(() => expect(text("token")).toBe("none"));
    expect(text("user")).toBe("none");
    expect(sessionStorage.getItem("perf_lab_access_token")).toBeNull();
  });
});
