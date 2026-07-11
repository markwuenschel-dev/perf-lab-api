// src/api/perfLabClient.ts
import { notifyUnauthorized } from "../auth/sessionBridge";
import type {
  ApiError,
  AuthorizeUrlResponse,
  BenchmarkObservationRead,
  BlockCreateRequest,
  BlockRead,
  BlockUpdateRequest,
  ComputeMetricsRequest,
  ConnectionStatus,
  ExerciseCatalogOut,
  MacrocycleCreate,
  MacrocycleRead,
  MacrocycleUpdate,
  MetricsResponse,
  ObjectiveCreate,
  ObjectiveRead,
  ObjectiveUpdate,
  OnboardRequest,
  OnboardResponse,
  OverviewMetrics,
  PlannedSessionRead,
  PlannedSessionUpdateRequest,
  ProfileRead,
  ProfileUpdate,
  ProjectionRequest,
  ProjectionResponse,
  ReadinessScore,
  StressDose,
  SyncResult,
  TokenResponse,
  TodaySessionResponse,
  UnifiedStateVector,
  UserResponse,
  WearableConnectionOut,
  WellnessSampleIn,
  WellnessSampleOut,
  WorkoutLog,
  WorkoutLogSummary,
  WorkoutPrescription,
} from "../types";

const RAW_BASE = import.meta.env.VITE_API_BASE_URL as string | undefined;
const API_ROOT = RAW_BASE ? RAW_BASE.replace(/\/$/, "") : "";

// Monolith deploy: the SPA is served same-origin by the API host (embedded at /static), so no
// absolute base URL is baked into the build. Fall back to relative ("same-origin") request paths
// — `${API_ROOT}/ping` becomes `/ping`, `${API_V1_BASE}/…` becomes `/v1/…`. Guard on PROD so that
// a missing base in local dev (where the API is a separate origin, :8000) still fails loudly
// instead of silently hitting the Vite dev server.
const SAME_ORIGIN = !API_ROOT && import.meta.env.PROD;
const IS_CONFIGURED = Boolean(API_ROOT) || SAME_ORIGIN;
const API_V1_BASE = API_ROOT ? `${API_ROOT}/v1` : SAME_ORIGIN ? "/v1" : "";

if (!IS_CONFIGURED) {
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

    // FastAPI returns `detail` as a string for HTTPException, but as an array of
    // {loc,msg,type} objects for 422 validation errors. Flatten both to a string
    // so callers (and the React error UI) never try to render a raw object.
    const rawDetail = (detail as { detail?: unknown })?.detail;
    let message: string;
    if (typeof rawDetail === "string") {
      message = rawDetail;
    } else if (Array.isArray(rawDetail)) {
      message = rawDetail
        .map((e) => (e as { msg?: string })?.msg ?? String(e))
        .join("; ");
    } else {
      message = res.statusText || "API request failed";
    }

    const error: ApiError = {
      message,
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
  if (!IS_CONFIGURED) {
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
  if (!IS_CONFIGURED) {
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
  if (!IS_CONFIGURED) {
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
  if (!IS_CONFIGURED) {
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
  if (!IS_CONFIGURED) {
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
 * Exercise catalog (ADR-0045): the movement library backing the per-set log UI.
 * Public — the catalog is seed data. `q`/`loadType`/`modality` filter server-side.
 */
export async function listExercises(
  opts?: { q?: string; loadType?: string; modality?: string },
): Promise<ExerciseCatalogOut[]> {
  if (!API_V1_BASE) {
    throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  }
  const params = new URLSearchParams();
  if (opts?.q) params.set("q", opts.q);
  if (opts?.loadType) params.set("load_type", opts.loadType);
  if (opts?.modality) params.set("modality", opts.modality);
  const qs = params.toString();
  const res = await fetch(`${API_V1_BASE}/exercises${qs ? `?${qs}` : ""}`);
  return handleResponse<ExerciseCatalogOut[]>(res);
}

/**
 * Twin Simulator (Phase 7): forward-project the eight capacity axes + readiness /
 * peak fatigue under a hypothetical plan. Auth-required — projects against the
 * athlete's seeded twin.
 */
export async function getSimulateProjection(
  body: ProjectionRequest,
  token: string,
): Promise<ProjectionResponse> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/simulate/projection`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<ProjectionResponse>(res, { sessionOn401: true });
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

/** The athlete's recent state vectors, oldest→newest (Twin time-travel, trends). */
export async function getStateHistory(
  token: string,
  limit?: number,
): Promise<UnifiedStateVector[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const query = limit != null ? `?limit=${limit}` : "";
  const res = await fetch(`${API_V1_BASE}/state-history${query}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<UnifiedStateVector[]>(res, { sessionOn401: true });
}

/** The athlete's logged workouts, most recent first (recent sessions, load). */
export async function listWorkouts(
  token: string,
  limit?: number,
): Promise<WorkoutLogSummary[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const query = limit != null ? `?limit=${limit}` : "";
  const res = await fetch(`${API_V1_BASE}/workouts${query}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<WorkoutLogSummary[]>(res, { sessionOn401: true });
}

/** Past benchmark observations (field tests), newest first. Optionally filter by
 *  benchmark_code. Feeds the History VO₂ progression + field-test log. */
export async function listBenchmarkObservations(
  token: string,
  opts?: { benchmarkCode?: string; limit?: number },
): Promise<BenchmarkObservationRead[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const q = new URLSearchParams();
  if (opts?.benchmarkCode) q.set("benchmark_code", opts.benchmarkCode);
  if (opts?.limit != null) q.set("limit", String(opts.limit));
  const query = q.toString() ? `?${q}` : "";
  const res = await fetch(`${API_V1_BASE}/benchmarks/observations${query}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<BenchmarkObservationRead[]>(res, { sessionOn401: true });
}

/** Load the authenticated athlete's profile (Settings hydrates from this). */
export async function getProfile(token: string): Promise<ProfileRead> {
  if (!API_V1_BASE) {
    throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  }
  const res = await fetch(`${API_V1_BASE}/profile`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<ProfileRead>(res, { sessionOn401: true });
}

/** Partial-update the athlete's profile; returns the saved row. */
export async function updateProfile(
  patch: ProfileUpdate,
  token: string,
): Promise<ProfileRead> {
  if (!API_V1_BASE) {
    throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  }
  const res = await fetch(`${API_V1_BASE}/profile`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(patch),
  });
  return handleResponse<ProfileRead>(res, { sessionOn401: true });
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

/**
 * Dashboard (P6): the Overview tiles' backend-owned metrics — acute:chronic
 * training load vs the sweet spot, plus recent plan adherence + streak. Fields
 * are null / `status == "insufficient"` for new users with little history.
 */
export async function getDashboardOverview(token: string): Promise<OverviewMetrics> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/dashboard/overview`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<OverviewMetrics>(res, { sessionOn401: true });
}

/**
 * Objectives (P4a): create a benchmark-linked or free-text goal (a race, a
 * meet, a Hyrox, a PR). Progress only computes when `benchmark_code` is set.
 */
export async function createObjective(
  body: ObjectiveCreate,
  token: string,
): Promise<ObjectiveRead> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/objectives`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<ObjectiveRead>(res, { sessionOn401: true });
}

/** Objectives (P4a): list the athlete's objectives, optionally filtered by status. */
export async function listObjectives(
  token: string,
  status?: string,
): Promise<ObjectiveRead[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const res = await fetch(`${API_V1_BASE}/objectives${query}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<ObjectiveRead[]>(res, { sessionOn401: true });
}

/** Objectives (P4a): partial-update an objective (e.g. mark achieved/abandoned). */
export async function updateObjective(
  id: number,
  body: ObjectiveUpdate,
  token: string,
): Promise<ObjectiveRead> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/objectives/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<ObjectiveRead>(res, { sessionOn401: true });
}

/** Objectives (P4a): delete an objective. */
export async function deleteObjective(id: number, token: string): Promise<void> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/objectives/${id}`, {
    method: "DELETE",
    headers: { ...authHeaders(token) },
  });
  return handleResponse<void>(res, { sessionOn401: true });
}

/* ---------- Wearable sync / Oura (Phase 2) ---------- */

/** Oura: the OAuth authorize URL to open in the browser (identity is in a signed state). */
export async function getOuraAuthorizeUrl(token: string): Promise<AuthorizeUrlResponse> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/integrations/oura/authorize`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<AuthorizeUrlResponse>(res, { sessionOn401: true });
}

/** Oura: connect via a Personal Access Token (single-user fast path). */
export async function connectOuraPat(
  patToken: string,
  token: string,
): Promise<WearableConnectionOut> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/integrations/oura/connect/pat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ token: patToken }),
  });
  return handleResponse<WearableConnectionOut>(res, { sessionOn401: true });
}

/** Oura: pull the athlete's recent data now (the nightly cron does this too). */
export async function syncOura(token: string): Promise<SyncResult> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/integrations/oura/sync`, {
    method: "POST",
    headers: { ...authHeaders(token) },
  });
  return handleResponse<SyncResult>(res, { sessionOn401: true });
}

/** Oura: the current connection status (no tokens are ever returned). */
export async function getWearableConnection(token: string): Promise<ConnectionStatus> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/integrations/oura/connection`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<ConnectionStatus>(res, { sessionOn401: true });
}

/** Oura: disconnect (deletes the stored connection + tokens). */
export async function disconnectOura(token: string): Promise<void> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/integrations/oura/connection`, {
    method: "DELETE",
    headers: { ...authHeaders(token) },
  });
  return handleResponse<void>(res, { sessionOn401: true });
}

/**
 * Macrocycles (P5): create a program container anchored to an objective. Yields
 * a real cross-block "week X of Y" (open horizon when the objective has no
 * target_date). Optional `start_date` defaults server-side.
 */
export async function createMacrocycle(
  body: MacrocycleCreate,
  token: string,
): Promise<MacrocycleRead> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/macrocycles`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<MacrocycleRead>(res, { sessionOn401: true });
}

/** Macrocycles (P5): list the athlete's macrocycles (active by default). */
export async function listMacrocycles(
  token: string,
  status?: string,
): Promise<MacrocycleRead[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const res = await fetch(`${API_V1_BASE}/macrocycles${query}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<MacrocycleRead[]>(res, { sessionOn401: true });
}

/** Macrocycles (P5): fetch one macrocycle by id. */
export async function getMacrocycle(
  id: number,
  token: string,
): Promise<MacrocycleRead> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/macrocycles/${id}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<MacrocycleRead>(res, { sessionOn401: true });
}

/** Macrocycles (P5): partial-update a macrocycle (e.g. re-anchor start_date, mark achieved). */
export async function updateMacrocycle(
  id: number,
  body: MacrocycleUpdate,
  token: string,
): Promise<MacrocycleRead> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/macrocycles/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<MacrocycleRead>(res, { sessionOn401: true });
}

/** Macrocycles (P5): delete a macrocycle. */
export async function deleteMacrocycle(id: number, token: string): Promise<void> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/macrocycles/${id}`, {
    method: "DELETE",
    headers: { ...authHeaders(token) },
  });
  return handleResponse<void>(res, { sessionOn401: true });
}
