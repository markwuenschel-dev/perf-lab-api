// src/api/perfLabClient.ts
import { notifyUnauthorized } from "../auth/sessionBridge";
import type {
  ApiError,
  BlockCreateRequest,
  BlockRead,
  BlockUpdateRequest,
  ComputeMetricsRequest,
  MetricsResponse,
  OnboardRequest,
  OnboardResponse,
  PlannedSessionRead,
  PlannedSessionUpdateRequest,
  ReadinessScore,
  StressDose,
  TokenResponse,
  TodaySessionResponse,
  UnifiedStateVector,
  UserResponse,
  WellnessSampleIn,
  WellnessSampleOut,
  WorkoutLog,
  WorkoutPrescription,
} from "../types";

const RAW_BASE = import.meta.env.VITE_API_BASE_URL as string | undefined;
const API_ROOT = RAW_BASE ? RAW_BASE.replace(/\/$/, "") : "";
const API_V1_BASE = API_ROOT ? `${API_ROOT}/v1` : "";

if (!API_ROOT) {
  console.warn("VITE_API_BASE_URL is not set. API calls will fail.");
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

type HandleOpts = {
  /** If true, notify auth layer to clear session on 401 */
  sessionOn401?: boolean;
};

async function handleResponse<T>(
  res: Response,
  opts?: HandleOpts,
): Promise<T> {
  if (res.status === 401 && opts?.sessionOn401) {
    notifyUnauthorized();
  }

  const contentType = res.headers.get("content-type");
  const isJson = contentType && contentType.includes("application/json");

  if (!res.ok) {
    let detail: unknown;
    if (isJson) {
      try {
        detail = await res.json();
      } catch {
        // ignore
      }
    } else {
      try {
        detail = await res.text();
      } catch {
        // ignore
      }
    }

    const error: ApiError = {
      message:
        (detail as { detail?: string })?.detail ??
        res.statusText ??
        "API request failed",
      status: res.status,
      details: detail,
    };
    throw error;
  }

  if (isJson) {
    return res.json() as Promise<T>;
  }

  return undefined as unknown as T;
}

export type PingResponse = {
  status: string;
};

export async function ping(): Promise<PingResponse> {
  if (!API_ROOT) {
    throw new Error("VITE_API_BASE_URL is not configured");
  }
  const res = await fetch(`${API_ROOT}/ping`);
  return handleResponse<PingResponse>(res);
}

/**
 * Field test: compute VO2 / fatigue / pace zones from a 300 m + 1.5 mi test.
 * Served by the legacy router (no /v1 prefix).
 */
export async function computeMetrics(
  req: ComputeMetricsRequest,
): Promise<MetricsResponse> {
  if (!API_ROOT) {
    throw new Error("VITE_API_BASE_URL is not configured");
  }
  const res = await fetch(`${API_ROOT}/compute-metrics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return handleResponse<MetricsResponse>(res);
}

export async function register(
  email: string,
  password: string,
): Promise<UserResponse> {
  if (!API_ROOT) {
    throw new Error("VITE_API_BASE_URL is not configured");
  }
  const res = await fetch(`${API_ROOT}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return handleResponse<UserResponse>(res);
}

export async function login(
  email: string,
  password: string,
): Promise<TokenResponse> {
  if (!API_ROOT) {
    throw new Error("VITE_API_BASE_URL is not configured");
  }
  const body = new URLSearchParams();
  body.set("username", email);
  body.set("password", password);
  const res = await fetch(`${API_ROOT}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  return handleResponse<TokenResponse>(res);
}

export async function fetchMe(token: string): Promise<UserResponse> {
  if (!API_ROOT) {
    throw new Error("VITE_API_BASE_URL is not configured");
  }
  const res = await fetch(`${API_ROOT}/auth/me`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<UserResponse>(res, { sessionOn401: true });
}

/**
 * Digital Twin: Controller – get recommended next session u_t.
 */
export async function getNextSession(
  goal: string,
  token: string,
): Promise<WorkoutPrescription> {
  if (!API_V1_BASE) {
    throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  }
  const url = `${API_V1_BASE}/next-session?goal=${encodeURIComponent(goal)}`;
  const res = await fetch(url, { headers: { ...authHeaders(token) } });
  return handleResponse<WorkoutPrescription>(res, { sessionOn401: true });
}

/**
 * Digital Twin: Log a workout, update S_t -> S_{t+1}, return new state.
 */
export async function logWorkout(
  log: WorkoutLog,
  token: string,
): Promise<UnifiedStateVector> {
  if (!API_V1_BASE) {
    throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  }
  const res = await fetch(`${API_V1_BASE}/log-workout`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token),
    },
    body: JSON.stringify(log),
  });
  return handleResponse<UnifiedStateVector>(res, { sessionOn401: true });
}

/**
 * Onboarding: create athlete profile and seed baseline state.
 */
export async function onboard(request: OnboardRequest): Promise<OnboardResponse> {
  if (!API_V1_BASE) {
    throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  }
  const res = await fetch(`${API_V1_BASE}/onboard`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  return handleResponse<OnboardResponse>(res);
}

/**
 * Digital Twin: Pure sensor map – compute D_t from a log without updating S_t.
 */
export async function simulateDose(log: WorkoutLog): Promise<StressDose> {
  if (!API_V1_BASE) {
    throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  }
  const res = await fetch(`${API_V1_BASE}/simulate-dose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(log),
  });
  return handleResponse<StressDose>(res);
}

export async function createPlanningBlock(
  body: BlockCreateRequest,
  token: string,
): Promise<BlockRead> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/planning/blocks`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<BlockRead>(res, { sessionOn401: true });
}

export async function listPlanningBlocks(token: string): Promise<BlockRead[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/planning/blocks`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<BlockRead[]>(res, { sessionOn401: true });
}

export async function updatePlanningBlock(
  blockId: number,
  body: BlockUpdateRequest,
  token: string,
): Promise<BlockRead> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/planning/blocks/${blockId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<BlockRead>(res, { sessionOn401: true });
}

type SessionListParams = {
  start_date?: string;
  end_date?: string;
};

export async function listPlannedSessions(
  token: string,
  params?: SessionListParams,
): Promise<PlannedSessionRead[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const query = new URLSearchParams();
  if (params?.start_date) query.set("start_date", params.start_date);
  if (params?.end_date) query.set("end_date", params.end_date);
  const url = `${API_V1_BASE}/planning/sessions${query.toString() ? `?${query.toString()}` : ""}`;
  const res = await fetch(url, { headers: { ...authHeaders(token) } });
  return handleResponse<PlannedSessionRead[]>(res, { sessionOn401: true });
}

export async function updatePlannedSession(
  sessionId: number,
  body: PlannedSessionUpdateRequest,
  token: string,
): Promise<PlannedSessionRead> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/planning/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<PlannedSessionRead>(res, { sessionOn401: true });
}

export async function getTodayPlannedSession(
  goal: string,
  token: string,
): Promise<TodaySessionResponse> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const url = `${API_V1_BASE}/planning/today?goal=${encodeURIComponent(goal)}`;
  const res = await fetch(url, { headers: { ...authHeaders(token) } });
  return handleResponse<TodaySessionResponse>(res, { sessionOn401: true });
}

/**
 * Wellness (P5): ingest one acute daily-wellness sample. Idempotent on
 * (date, source) — re-posting the same day/source replaces it.
 */
export async function ingestWellness(
  body: WellnessSampleIn,
  token: string,
): Promise<WellnessSampleOut> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/wellness`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<WellnessSampleOut>(res, { sessionOn401: true });
}

/** Wellness (P5): recent daily-wellness samples (most recent first). */
export async function listWellness(
  token: string,
  limit?: number,
): Promise<WellnessSampleOut[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const query = limit != null ? `?limit=${limit}` : "";
  const res = await fetch(`${API_V1_BASE}/wellness${query}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<WellnessSampleOut[]>(res, { sessionOn401: true });
}

/**
 * Readiness (P5): the one backend-owned readiness number — modeled fatigue
 * combined with acute wellness (ADR-0026). `readiness` is null when there is
 * no modeled state to anchor against.
 */
export async function getReadiness(token: string): Promise<ReadinessScore> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/readiness`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<ReadinessScore>(res, { sessionOn401: true });
}
