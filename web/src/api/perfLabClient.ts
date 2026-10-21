// src/api/perfLabClient.ts
import { notifyUnauthorized } from "../auth/sessionBridge";
import { readAttemptResult } from "./ouraReply";
import type {
  ApiError,
  AssessmentSurfaceRead,
  AuthorizeUrlResponse,
  OAuthAttemptResult,
  OAuthCancelRequest,
  OAuthCompleteRequest,
  BenchmarkObservationCreate,
  BenchmarkObservationRead,
  CompleteOnboardingRequest,
  OnboardingStateResponse,
  BlockCreateRequest,
  BlockRead,
  BlockUpdateRequest,
  ComputeMetricsRequest,
  ConnectionStatus,
  DrivingObjectiveRead,
  IngestTokenCreated,
  IngestTokenOut,
  ExerciseCatalogOut,
  MacrocycleCreate,
  MacrocycleRead,
  MacrocycleUpdate,
  MetricsResponse,
  ObjectiveCreate,
  ObjectiveOrderUpdate,
  ObjectiveRead,
  ObjectiveUpdate,
  OnboardRequest,
  OnboardResponse,
  OverviewMetrics,
  PlannedSessionRead,
  PlannedSessionUpdateRequest,
  PlannedWeekProjection,
  ProfileRead,
  ProfileUpdate,
  TimezoneCapture,
  ProjectionRequest,
  ProjectionResponse,
  ReadinessScore,
  StateHistorySnapshotRead,
  StressDose,
  StrengthEvidenceCreate,
  StrengthOutcome,
  SyncResult,
  TokenResponse,
  TodaySessionResponse,
  LogWorkoutResponse,
  UnderivedStrengthReportRead,
  UserResponse,
  SessionFeedbackIn,
  SessionFeedbackOut,
  WeeklyTemplateSlotPreview,
  WeekReview,
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
  /** The bearer token this request was sent with. On a 401 the auth layer is told which token
   *  was refused, and signs out only if it is still the current session's. */
  sessionOn401?: string;
};

async function handleResponse<T>(
  res: Response,
  opts?: HandleOpts,
): Promise<T> {
  if (res.status === 401 && opts?.sessionOn401) {
    notifyUnauthorized(opts.sessionOn401);
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

  // 204 No Content is the ONLY empty success body this client accepts. FastAPI builds a 204
  // with its default JSON response class, so the reply still carries `content-type:
  // application/json` while the body is empty (starlette drops the body, not the header) —
  // parsing it threw "Unexpected end of JSON input" on every DELETE. Narrow by status, not by
  // emptiness: an empty or malformed 200 where a payload is expected stays a contract failure.
  if (res.status === 204) {
    return undefined as unknown as T;
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

export async function fetchMe(token: string, signal?: AbortSignal): Promise<UserResponse> {
  if (!IS_CONFIGURED) {
    throw new Error("VITE_API_BASE_URL is not configured");
  }
  const res = await fetch(`${API_ROOT}/auth/me`, {
    headers: { ...authHeaders(token) },
    signal,
  });
  return handleResponse<UserResponse>(res, { sessionOn401: token });
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
  return handleResponse<WorkoutPrescription>(res, { sessionOn401: token });
}

/**
 * Digital Twin: log a workout. Returns the current state after the log plus what the log did:
 * `state_disposition` is `record_only` when the training-state update was omitted (P3a).
 */
export async function logWorkout(
  log: WorkoutLog,
  token: string,
): Promise<LogWorkoutResponse> {
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
  return handleResponse<LogWorkoutResponse>(res, { sessionOn401: token });
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
  return handleResponse<ProjectionResponse>(res, { sessionOn401: token });
}

/**
 * Onboarding: create athlete profile and seed baseline state. The route identifies the
 * athlete by the bearer token — without it the request is refused, and nothing is saved.
 */
export async function onboard(request: OnboardRequest, token: string): Promise<OnboardResponse> {
  if (!API_V1_BASE) {
    throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  }
  const res = await fetch(`${API_V1_BASE}/onboard`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(request),
  });
  return handleResponse<OnboardResponse>(res, { sessionOn401: token });
}

/** The athlete's recent state snapshots, oldest→newest (Twin time-travel, trends).
 *  Each row is a `StateHistorySnapshotRead` — the canonical `UnifiedStateVector`
 *  (a structural superset, so Overview/History that read only vector fields keep
 *  compiling) plus this snapshot's `snapshot_id`, per-axis confidence-presentation
 *  bands, and the policy version (ADR-0059). */
export async function getStateHistory(
  token: string,
  limit?: number,
): Promise<StateHistorySnapshotRead[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const query = limit != null ? `?limit=${limit}` : "";
  const res = await fetch(`${API_V1_BASE}/state-history${query}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<StateHistorySnapshotRead[]>(res, { sessionOn401: token });
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
  return handleResponse<WorkoutLogSummary[]>(res, { sessionOn401: token });
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
  return handleResponse<BenchmarkObservationRead[]>(res, { sessionOn401: token });
}

/** Load the authenticated athlete's profile (Settings hydrates from this). */
export async function getProfile(token: string, signal?: AbortSignal): Promise<ProfileRead> {
  if (!API_V1_BASE) {
    throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  }
  const res = await fetch(`${API_V1_BASE}/profile`, {
    headers: { ...authHeaders(token) },
    signal,
  });
  return handleResponse<ProfileRead>(res, { sessionOn401: token });
}

/**
 * Offer the browser's IANA timezone (B1). The backend stores it only while the athlete's zone
 * is unknown and never replaces a known one; it returns the stored profile either way.
 */
export async function captureDetectedTimezone(
  body: TimezoneCapture,
  token: string,
): Promise<ProfileRead> {
  if (!API_V1_BASE) {
    throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  }
  const res = await fetch(`${API_V1_BASE}/profile/timezone/detected`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<ProfileRead>(res, { sessionOn401: token });
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
  return handleResponse<ProfileRead>(res, { sessionOn401: token });
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
  return handleResponse<BlockRead>(res, { sessionOn401: token });
}

/** Planning: the week a block request WOULD generate. Persists nothing — the create screen
 *  shows the server's own allocation instead of reimplementing it here and drifting. */
export async function previewPlanningBlock(
  body: BlockCreateRequest,
  token: string,
): Promise<WeeklyTemplateSlotPreview[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/planning/blocks/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<WeeklyTemplateSlotPreview[]>(res, { sessionOn401: token });
}

export async function listPlanningBlocks(token: string): Promise<BlockRead[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/planning/blocks`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<BlockRead[]>(res, { sessionOn401: token });
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
  return handleResponse<BlockRead>(res, { sessionOn401: token });
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
  return handleResponse<PlannedSessionRead[]>(res, { sessionOn401: token });
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
  return handleResponse<PlannedSessionRead>(res, { sessionOn401: token });
}

/**
 * C1b (ADR-0073): the athlete's PENDING planned sessions projected forward through
 * the real engine — per-day sRPE load and modeled MEAN FATIGUE (not readiness).
 * Display-only. `available: false` + `reason` ("no_state" | "state_invalid") is a
 * 200, not an error. `through` defaults server-side to the end of the current
 * block week (today+6 with no active block) and is capped at 28 days.
 */
export async function getPlannedWeekProjection(
  token: string,
  through?: string,
): Promise<PlannedWeekProjection> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const query = through ? `?through=${encodeURIComponent(through)}` : "";
  const res = await fetch(`${API_V1_BASE}/planning/projection${query}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<PlannedWeekProjection>(res, { sessionOn401: token });
}

/**
 * Week review: one block week — its sessions, counts, what the state did, and the facts
 * already determined for the following week. With both params omitted the backend picks
 * the current block's current week. Display-only. No active block / no state / undecodable
 * state come back as `200 {available: false, reason}`; an unknown block or out-of-range
 * week is a 404.
 */
export async function getWeekReview(
  token: string,
  params?: { block_id?: number; week_number?: number },
): Promise<WeekReview> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const query = new URLSearchParams();
  if (params?.block_id != null) query.set("block_id", String(params.block_id));
  if (params?.week_number != null) query.set("week_number", String(params.week_number));
  const url = `${API_V1_BASE}/planning/week-review${query.toString() ? `?${query.toString()}` : ""}`;
  const res = await fetch(url, { headers: { ...authHeaders(token) } });
  return handleResponse<WeekReview>(res, { sessionOn401: token });
}

export async function getTodayPlannedSession(
  goal: string,
  token: string,
): Promise<TodaySessionResponse> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const url = `${API_V1_BASE}/planning/today?goal=${encodeURIComponent(goal)}`;
  const res = await fetch(url, { headers: { ...authHeaders(token) } });
  return handleResponse<TodaySessionResponse>(res, { sessionOn401: token });
}

/**
 * P1: ask for today's session to be re-checked. Unlike GET /planning/today, this may LIFT a
 * safety restriction that has cleared (the server replaces the issued revision with the
 * unrestricted one). Only offered while a restriction is in force (todayRevision.canRecheck).
 */
export async function recheckTodayPlannedSession(
  goal: string,
  token: string,
): Promise<TodaySessionResponse> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const url = `${API_V1_BASE}/planning/today/recheck?goal=${encodeURIComponent(goal)}`;
  const res = await fetch(url, { method: "POST", headers: { ...authHeaders(token) } });
  return handleResponse<TodaySessionResponse>(res, { sessionOn401: token });
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
  return handleResponse<WellnessSampleOut>(res, { sessionOn401: token });
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
  return handleResponse<WellnessSampleOut[]>(res, { sessionOn401: token });
}

/* ---------- Session feedback (athlete-reported outcomes) ---------- */

/**
 * Report the outcome of one planned session. The session must already be
 * completed or skipped — feedback describes an outcome, it never creates one,
 * and it never changes the session's status (ADR-0070). A 409 means either the
 * session has not happened yet or feedback was already recorded for it.
 */
export async function createSessionFeedback(
  body: SessionFeedbackIn,
  token: string,
): Promise<SessionFeedbackOut> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<SessionFeedbackOut>(res, { sessionOn401: token });
}

/** The athlete's recent reported session outcomes, newest first. */
export async function listSessionFeedback(
  token: string,
  limit?: number,
): Promise<SessionFeedbackOut[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const query = limit != null ? `?limit=${limit}` : "";
  const res = await fetch(`${API_V1_BASE}/feedback${query}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<SessionFeedbackOut[]>(res, { sessionOn401: token });
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
  return handleResponse<ReadinessScore>(res, { sessionOn401: token });
}

/**
 * Dashboard (P6): the Overview tiles' backend-owned metrics — acute:chronic
 * training load vs a heuristic reference band, plus recent plan adherence + streak. Fields
 * are null / `status == "insufficient"` for new users with little history.
 */
export async function getDashboardOverview(token: string): Promise<OverviewMetrics> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/dashboard/overview`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<OverviewMetrics>(res, { sessionOn401: token });
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
  return handleResponse<ObjectiveRead>(res, { sessionOn401: token });
}

/** Benchmark assessment surface (P10, ADR-0047): the one domain-filtered catalog view
 *  with a measurement-debt ranking. `mode` is product framing only (onramp vs retest). */
export async function getAssessmentSurface(
  token: string,
  mode: "onramp" | "retest" = "onramp",
): Promise<AssessmentSurfaceRead> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/benchmarks/assessment-surface?mode=${mode}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<AssessmentSurfaceRead>(res, { sessionOn401: token });
}

/** Submit a benchmark assessment as a single observation (P10). The backend owns the
 *  state seed/update via the ADR-0058 authority path; the frontend never seeds capacity. */
export async function submitBenchmarkObservation(
  body: BenchmarkObservationCreate,
  token: string,
): Promise<BenchmarkObservationRead> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/benchmarks/observations`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<BenchmarkObservationRead>(res, { sessionOn401: token });
}

/** Report a strength observation for a canonical lift from Assess (S2). Build the body
 *  with `strengthEvidenceBody`: it states what happened, and the server derives the value
 *  semantics, the estimate, and whether it may size a load. Evidence only — no workout.
 *  A set with unknown or conflicting effort comes back `report_only` (P4-3). Send the
 *  body through `useSubmissionKey` so a retry carries the same `idempotency_key`. */
export async function submitStrengthEvidence(
  body: StrengthEvidenceCreate,
  token: string,
): Promise<StrengthOutcome> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/benchmarks/strength-evidence`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  const reply = await handleResponse<unknown>(res, { sessionOn401: token });
  if (!isStrengthOutcome(reply)) {
    // A 2xx whose body is not a StrengthOutcome confirms nothing: the save may or may not have
    // been recorded. No `status`, so a keyed caller keeps the attempt for Retry.
    const error: ApiError = {
      message: "The server's reply to this save couldn't be read, so it may or may not be recorded.",
      details: reply,
    };
    throw error;
  }
  return reply;
}

/** Whether a reply is a StrengthOutcome the way the server builds one: every field of the
 *  record it carries has the type `BenchmarkObservationRead` / `UnderivedStrengthReportRead`
 *  declare (app/schemas/benchmarks.py:184-221 — integer ids and reps, a report's method is
 *  `rep_set`, its reason one of the two), and `StrengthOutcome`'s validator holds (:245-257):
 *  exactly one record, an observation carrying its value and no reason, a report carrying its
 *  reason and no value. Anything else confirms nothing. */
export function isStrengthOutcome(reply: unknown): reply is StrengthOutcome {
  const reasons = ["effort_unknown", "effort_conflict"];
  const obj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
  const int = (v: unknown) => typeof v === "number" && Number.isInteger(v);
  const num = (v: unknown) => typeof v === "number" && Number.isFinite(v);
  const str = (v: unknown) => typeof v === "string";
  const time = (v: unknown) => typeof v === "string" && Number.isFinite(Date.parse(v));
  const orNull = (check: (v: unknown) => boolean) => (v: unknown) => v === null || check(v);
  // A field with a server-side default may be absent; when present it must have its type.
  const optional = (check: (v: unknown) => boolean) => (v: unknown) => v === undefined || v === null || check(v);

  function isObservation(o: unknown): boolean {
    return (
      obj(o) &&
      int(o.id) && int(o.user_id) && int(o.benchmark_definition_id) && str(o.benchmark_code) &&
      time(o.observed_at) && num(o.raw_value) && orNull(num)(o.secondary_value) &&
      orNull(num)(o.normalized_value) && str(o.validity_status) && str(o.source) &&
      optional((v) => v === "applied" || v === "record_only")(o.state_disposition) &&
      optional(str)(o.state_disposition_reason)
    );
  }

  function isReport(o: unknown): boolean {
    return (
      obj(o) &&
      int(o.id) && str(o.benchmark_code) && o.method === "rep_set" && str(o.collection_mode) &&
      orNull(time)(o.performed_at) && num(o.load_kg) && int(o.reps) && orNull(num)(o.rpe) &&
      orNull(num)(o.rir) && reasons.includes(o.reason as string) && time(o.created_at) &&
      orNull(int)(o.superseded_by_observation_id) && orNull(time)(o.superseded_at)
    );
  }

  if (!obj(reply)) return false;
  if (!["recorded", "report_only", "already_recorded"].includes(reply.outcome as string)) return false;
  if (typeof reply.value_available !== "boolean") return false;
  if (!optional((v) => reasons.includes(v as string))(reply.reason)) return false;
  const observation = reply.observation ?? null;
  const report = reply.report ?? null;
  if ((observation === null) === (report === null)) return false;
  if (observation !== null) {
    return (
      isObservation(observation) &&
      reply.value_available === true && reply.reason == null && reply.outcome !== "report_only"
    );
  }
  return (
    isReport(report) &&
    reply.value_available === false && reply.outcome !== "recorded" &&
    (reply.outcome !== "report_only" || reply.reason != null)
  );
}

/** The athlete's strength reports that yielded no estimate (P4-3), most recently performed
 *  first, undated last; optionally for one lift. */
export async function listStrengthReports(
  token: string,
  benchmarkCode?: string,
): Promise<UnderivedStrengthReportRead[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const query = benchmarkCode ? `?benchmark_code=${encodeURIComponent(benchmarkCode)}` : "";
  const res = await fetch(`${API_V1_BASE}/benchmarks/strength-reports${query}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<UnderivedStrengthReportRead[]>(res, { sessionOn401: token });
}

/** Non-blocking onboarding state (P10, PDR-0010): status, the safety hard-gate,
 *  the provisional twin summary, and progressive measurement-debt prompts. */
export async function getOnboardingState(token: string): Promise<OnboardingStateResponse> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/onboarding/state`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<OnboardingStateResponse>(res, { sessionOn401: token });
}

/** Leave onboarding with a reason (finished | done_for_now | skipped) — never locks out. */
export async function completeOnboarding(
  token: string,
  reason: CompleteOnboardingRequest["reason"] = "done_for_now",
): Promise<OnboardingStateResponse> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/onboarding/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ reason }),
  });
  return handleResponse<OnboardingStateResponse>(res, { sessionOn401: token });
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
  return handleResponse<ObjectiveRead[]>(res, { sessionOn401: token });
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
  return handleResponse<ObjectiveRead>(res, { sessionOn401: token });
}

/** Objectives: set the DISPLAY order of the caller's active objectives (`display_rank`
 *  1..N, ADR-0061). Display only — never touches `priority` or what drives prescription.
 *  `objectiveIds` must list every active objective exactly once, else 400 (refetch). */
export async function setObjectiveOrder(
  objectiveIds: number[],
  token: string,
): Promise<ObjectiveRead[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const body: ObjectiveOrderUpdate = { objective_ids: objectiveIds };
  const res = await fetch(`${API_V1_BASE}/objectives/order`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return handleResponse<ObjectiveRead[]>(res, { sessionOn401: token });
}

/** Objectives: the objective that actually drives prescription (program anchor, else
 *  priority) — the only honest source for a "primary" badge. Read-only. */
export async function getDrivingObjective(token: string): Promise<DrivingObjectiveRead> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/objectives/driving`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<DrivingObjectiveRead>(res, { sessionOn401: token });
}

/** Objectives (P4a): delete an objective. */
export async function deleteObjective(id: number, token: string): Promise<void> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/objectives/${id}`, {
    method: "DELETE",
    headers: { ...authHeaders(token) },
  });
  return handleResponse<void>(res, { sessionOn401: token });
}

/* ---------- Wearable sync / Oura (Phase 2) ---------- */

/** Oura: start an authorization attempt bound to this browser's PKCE challenge (S256) and get
 *  the consent URL to open. The caller must check the URL carries its own challenge. */
export async function getOuraAuthorizeUrl(
  token: string,
  codeChallenge: string,
): Promise<AuthorizeUrlResponse> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const query = new URLSearchParams({ code_challenge: codeChallenge, code_challenge_method: "S256" });
  const res = await fetch(`${API_V1_BASE}/integrations/oura/authorize?${query.toString()}`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<AuthorizeUrlResponse>(res, { sessionOn401: token });
}

/** A /complete or /cancel 200 that isn't a shape the server sends is not an outcome: it is
 *  thrown, so the caller keeps the attempt's inputs instead of treating it as terminal. */
async function attemptResult(res: Response, token: string): Promise<OAuthAttemptResult> {
  const body = await handleResponse<unknown>(res, { sessionOn401: token });
  const result = readAttemptResult(body);
  if (result === null) {
    const error: ApiError = { message: "Unexpected reply from Perf Lab", status: res.status, details: body };
    throw error;
  }
  return result;
}

/** Oura: finish the attempt the browser came back with (code + the PKCE verifier it kept).
 *  200 is a success receipt (history, not current state); 409/503 arrive as ApiError whose
 *  `details.detail` is `{status, reason}`. */
export async function completeOuraAuthorization(
  body: OAuthCompleteRequest,
  token: string,
): Promise<OAuthAttemptResult> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/integrations/oura/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return attemptResult(res, token);
}

/** Oura: the athlete declined at Oura. Returns where the attempt stands afterwards, which is
 *  not necessarily "cancelled". */
export async function cancelOuraAuthorization(
  body: OAuthCancelRequest,
  token: string,
): Promise<OAuthAttemptResult> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/integrations/oura/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(body),
  });
  return attemptResult(res, token);
}

/* ---------- Phone-pushed wellness (Apple Watch via an iOS Shortcut) ---------- */

/** The URL a phone automation posts to, absolute so it can be pasted into a Shortcut. */
export function wellnessIngestUrl(): string {
  const base = API_V1_BASE.startsWith("http") ? API_V1_BASE : `${window.location.origin}${API_V1_BASE}`;
  return `${base}/wellness/ingest`;
}

/** Create a write-only ingest token. The response is the ONLY time the token is shown. */
export async function createIngestToken(label: string, token: string): Promise<IngestTokenCreated> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/wellness/ingest-tokens`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ label }),
  });
  return handleResponse<IngestTokenCreated>(res, { sessionOn401: token });
}

/** The athlete's active ingest tokens; `last_used_at` is the last successful push. */
export async function listIngestTokens(token: string): Promise<IngestTokenOut[]> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/wellness/ingest-tokens`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<IngestTokenOut[]>(res, { sessionOn401: token });
}

/** Revoke an ingest token; the phone automation using it stops working immediately. */
export async function revokeIngestToken(id: number, token: string): Promise<void> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/wellness/ingest-tokens/${id}`, {
    method: "DELETE",
    headers: { ...authHeaders(token) },
  });
  return handleResponse<void>(res, { sessionOn401: token });
}

/** Oura: pull the athlete's recent data now (the app also does this when data is stale). */
export async function syncOura(token: string): Promise<SyncResult> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/integrations/oura/sync`, {
    method: "POST",
    headers: { ...authHeaders(token) },
  });
  return handleResponse<SyncResult>(res, { sessionOn401: token });
}

/** Oura: the current connection status (no tokens are ever returned). */
export async function getWearableConnection(token: string): Promise<ConnectionStatus> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/integrations/oura/connection`, {
    headers: { ...authHeaders(token) },
  });
  return handleResponse<ConnectionStatus>(res, { sessionOn401: token });
}

/** Oura: disconnect (deletes the stored connection + tokens). */
export async function disconnectOura(token: string): Promise<void> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/integrations/oura/connection`, {
    method: "DELETE",
    headers: { ...authHeaders(token) },
  });
  return handleResponse<void>(res, { sessionOn401: token });
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
  return handleResponse<MacrocycleRead>(res, { sessionOn401: token });
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
  return handleResponse<MacrocycleRead[]>(res, { sessionOn401: token });
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
  return handleResponse<MacrocycleRead>(res, { sessionOn401: token });
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
  return handleResponse<MacrocycleRead>(res, { sessionOn401: token });
}

/** Macrocycles (P5): delete a macrocycle. */
export async function deleteMacrocycle(id: number, token: string): Promise<void> {
  if (!API_V1_BASE) throw new Error("VITE_API_BASE_URL is not configured (no /v1 base)");
  const res = await fetch(`${API_V1_BASE}/macrocycles/${id}`, {
    method: "DELETE",
    headers: { ...authHeaders(token) },
  });
  return handleResponse<void>(res, { sessionOn401: token });
}
