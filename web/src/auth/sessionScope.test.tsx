// @vitest-environment jsdom
//
// Answers belong to the session that asked, through the REAL client, bridge and <AuthProvider>
// (only `fetch` is stubbed, routed by path and bearer token):
//   - A 401 answering a request made with an earlier session's token does not sign out the
//     current session; a 401 for the current token still does.
//   - Login's own /auth/me is bounded: a hung one clears the spinner and leaves a retryable
//     session, and a login's late answers after a logout + another login change nothing.
//   - The session load's own 401 sign-out is bound to the token that load used.
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

/** A request that never answers, but rejects like fetch does once its signal aborts. */
function hang(signal: AbortSignal | null | undefined): Promise<Response> {
  return new Promise((_, reject) => {
    signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  });
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

/** Runs `then` right after `token` is written to storage: before React commits it, i.e. while
 *  the previous token's effect has not been cleaned up yet. (A stub, because jsdom's Storage
 *  methods cannot be spied on.) */
function onTokenStored(token: string, then: () => void) {
  const items = new Map<string, string>();
  for (let i = 0; i < sessionStorage.length; i++) {
    const k = sessionStorage.key(i)!;
    items.set(k, sessionStorage.getItem(k)!);
  }
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => items.get(k) ?? null,
    setItem: (k: string, v: string) => {
      items.set(k, v);
      if (k === "perf_lab_access_token" && v === token) then();
    },
    removeItem: (k: string) => void items.delete(k),
    clear: () => items.clear(),
  });
}

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

describe("login's own /auth/me", () => {
  it("a hung one clears the spinner and leaves a retryable session", async () => {
    routes.me[TOKEN_B] = (init) => hang(init?.signal);
    await mount(null, 30);
    fireEvent.click(screen.getByText("login b"));
    await waitFor(() => expect(text("token")).toBe(TOKEN_B));
    // Not a stuck spinner: the login's check times out, and so does the session load's.
    await waitFor(() => expect(text("loading")).toBe("false"));
    await waitFor(() => expect(text("session")).toBe("network"));
    expect(text("user")).toBe("none");
    expect(sessionStorage.getItem("perf_lab_access_token")).toBe(TOKEN_B);

    // Retryable: once /auth/me answers, the retry confirms the session.
    routes.me[TOKEN_B] = () => json(200, USER_B);
    fireEvent.click(screen.getByText("retry"));
    await waitFor(() => expect(text("user")).toBe(USER_B.email));
    expect(text("session")).toBe("ok");
  });

  it("a login's /auth/me answering after a logout and another login changes nothing", async () => {
    const lateMeA = deferred();
    routes.me[TOKEN_A] = () => lateMeA.promise.then(() => json(200, USER_A));
    await mount(null);
    fireEvent.click(screen.getByText("login a"));
    await waitFor(() => expect(text("token")).toBe(TOKEN_A));

    fireEvent.click(screen.getByText("logout"));
    fireEvent.click(screen.getByText("login b"));
    await waitFor(() => expect(text("user")).toBe(USER_B.email));

    await act(async () => lateMeA.resolve());
    await act(async () => undefined);
    expect(text("user")).toBe(USER_B.email);
    expect(text("token")).toBe(TOKEN_B);
  });

  it("a login's /auth/token answering after another login does not replace the newer session", async () => {
    const lateTokenA = deferred();
    routes.token[USER_A.email] = () =>
      lateTokenA.promise.then(() => json(200, { access_token: TOKEN_A, token_type: "bearer" }));
    await mount(null);
    fireEvent.click(screen.getByText("login a"));
    fireEvent.click(screen.getByText("login b"));
    await waitFor(() => expect(text("user")).toBe(USER_B.email));

    await act(async () => lateTokenA.resolve());
    await act(async () => undefined);
    expect(text("token")).toBe(TOKEN_B);
    expect(sessionStorage.getItem("perf_lab_access_token")).toBe(TOKEN_B);
    expect(text("user")).toBe(USER_B.email);
  });
});

describe("the session load's own 401 sign-out", () => {
  it("an old token's /auth/me 401 landing after a new login stored its token leaves the new session", async () => {
    const lateA = deferred();
    routes.me[TOKEN_A] = () => lateA.promise.then(() => json(401, { detail: "Not authenticated" }));
    await mount(TOKEN_A); // A's session load is waiting on /auth/me
    onTokenStored(TOKEN_B, () => lateA.resolve());
    fireEvent.click(screen.getByText("login b"));
    await waitFor(() => expect(text("user")).toBe(USER_B.email));
    await act(async () => undefined);
    expect(text("token")).toBe(TOKEN_B);
    expect(sessionStorage.getItem("perf_lab_access_token")).toBe(TOKEN_B);
  });
});
