"""
app/main.py

Main FastAPI application entrypoint for Performance Lab API.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import (
    auth,
    benchmarks,
    dashboard,
    exercises,
    feedback,
    ingest,
    integrations_oura,
    legacy,
    macrocycles,
    objectives,
    planning,
    prescribe,
    shadow,
    simulate,
    weak_points,
    wellness,
)
from app.api.v1.history import router as history_router
from app.api.v1.onboard import router as onboard_router
from app.api.v1.profile import router as profile_router
from app.core.config import (
    CORS_NON_ORIGINS,
    CORS_REGEX_UNSUPPORTED_IN_PRODUCTION,
    DEBUG_UNSUPPORTED_IN_PRODUCTION,
    DEV_DEFAULT_ORIGINS,
    PUBLIC_EXAMPLE_SECRET_KEYS,
    Settings,
    settings,
)
from app.core.db import engine
from app.core.errors import CanonicalStateInvalid
from app.engine.engine_state_codec import EngineStateDecodeError

logger = logging.getLogger("perflab")

# A 256-bit floor for the HS256 signing key (32 chars). Below this — or the default /
# empty value — a key is too weak to sign auth tokens.
MIN_SECRET_KEY_LENGTH = 32


def _check_production_secrets(cfg: Settings) -> None:
    """Refuse to boot in production with a public, empty, or too-short SECRET_KEY (INT-01).

    ``SECRET_KEY`` signs every JWT (HS256); a published or trivially short key lets anyone
    forge tokens and impersonate any user. Mirror the ``_on_schema_mismatch`` contract:
    fail fast (raise) in production, log a warning elsewhere so local/dev boots aren't
    blocked. Generate a strong key with ``openssl rand -hex 32``.

    INT-A1: this refuses every key the repo publishes, not just ``DEFAULT_SECRET_KEY``.
    Enumerating one bad key meant the `.env.example` key — a *different* public string,
    57 chars and so past the length floor — booted production clean via the documented
    ``cp .env.example .env`` path. The rule is now "no published key may sign production
    tokens", and ``PUBLIC_EXAMPLE_SECRET_KEYS`` is pinned to `.env.example` by
    ``tests/test_production_boot_guards.py`` so it cannot fall behind that file.

    Deliberately NOT an entropy test: an operator's own weak key (``password123...``)
    still passes, and catching that needs judgement this guard should not make. It is a
    separate ledger candidate, not a silent extension of this one.
    """
    key = cfg.SECRET_KEY.strip()
    weak = (
        (not key)
        or key in PUBLIC_EXAMPLE_SECRET_KEYS
        or len(key) < MIN_SECRET_KEY_LENGTH
    )
    if not weak:
        return
    message = (
        "SECRET_KEY is unset, too short, or a value published in this repository "
        f"(needs a private random value of at least {MIN_SECRET_KEY_LENGTH} chars — e.g. "
        "`openssl rand -hex 32`). It signs every auth token; a published or weak key "
        "allows token forgery. Note .env.example's key is public: copying it is not enough."
    )
    if cfg.is_production:
        raise RuntimeError(message)
    logger.warning("%s (allowed outside production)", message)


def _check_production_debug(cfg: Settings) -> None:
    """Refuse to boot in production with debug logging enabled (INT-A3).

    ``DEBUG`` drives SQLAlchemy ``echo`` (``app/core/db.py``), which logs every statement
    and its bound parameters — so a production boot with it on writes ``hashed_password``
    on every user INSERT and wearable OAuth token ciphertext to the application log. Mirror
    ``_check_production_secrets``: fail fast (raise) in production, warn elsewhere so
    local/dev boots keep echo if they want it.

    ``DEBUG`` is a bool, so unlike SECRET_KEY there is nothing to grade — the safe set is
    exactly ``{False}`` and this refuses its complement. The type is what makes the
    enumeration closed, the same argument ``CORS_NON_ORIGINS`` makes from the spec. Note
    pydantic accepts ``yes``/``on``/``1`` as True from the environment, so checking the
    coerced field rather than the raw string is load-bearing.

    The companion half of this fix is the field default (now False). This guard cannot fire
    unless ``ENVIRONMENT=production`` is *also* set correctly; a fail-closed default is
    what covers the operator who sets neither. See ``app/core/config.py``.
    """
    if not cfg.DEBUG:
        return
    if cfg.is_production:
        raise RuntimeError(DEBUG_UNSUPPORTED_IN_PRODUCTION)
    logger.warning("%s (allowed outside production)", DEBUG_UNSUPPORTED_IN_PRODUCTION)


def _cors_problem(cfg: Settings) -> str | None:
    """Why this CORS config must not serve production, or None if it is safe.

    Order matters: a permissive origin is reported before a missing one, because
    ``ALLOWED_ORIGINS=*`` satisfies "an explicit origin is pinned" while being strictly
    worse than pinning nothing.
    """
    origins = cfg.allowed_origins_list

    # INT-A1: neither `*` nor `null` is a dev default, so the old "is anything non-dev
    # pinned?" test accepted both — the two most permissive values possible passing a
    # guard that exists to require a restrictive one. A pinned origin sitting beside
    # either does not neutralise it. See CORS_NON_ORIGINS for why the set is closed.
    non_origins = sorted(CORS_NON_ORIGINS & {o.strip().lower() for o in origins})
    if non_origins:
        listed = ", ".join(f"`{o}`" for o in non_origins)
        return (
            f"ALLOWED_ORIGINS contains {listed}, which is not a real origin and allows "
            "callers you have not pinned. Combined with credentialed requests this hands "
            "them authenticated access. Pin explicit origins, e.g. "
            "set ALLOWED_ORIGINS=https://perflab.44-198-76-44.nip.io."
        )

    # INT-A1: a regex is refused outright rather than inspected. Judging a pattern's
    # narrowness — by syntax or by probing it — only ever clears the shapes the author
    # thought of, which is the accept-unless-recognised idiom this guard exists to remove.
    # See CORS_REGEX_UNSUPPORTED_IN_PRODUCTION.
    if cfg.allowed_origin_regex is not None:
        return CORS_REGEX_UNSUPPORTED_IN_PRODUCTION

    dev_defaults = {o.strip().lower() for o in DEV_DEFAULT_ORIGINS}
    if not any(o.strip().lower() not in dev_defaults for o in origins):
        return (
            "No explicit production CORS origin is configured; only local-dev origins are "
            "allowed. Pin the prod origin, e.g. "
            "set ALLOWED_ORIGINS=https://perflab.44-198-76-44.nip.io. "
            "A regex alone is not accepted — an explicit origin must be pinned."
        )

    return None


def _check_production_cors(cfg: Settings) -> None:
    """Refuse to boot in production on a permissive or unpinned CORS config (INT-09).

    Production must pin at least one explicit allowed origin (e.g.
    ``https://perflab.44-198-76-44.nip.io``) rather than relying on the old catch-all
    wildcard-subdomain regex default. Mirror ``_check_production_secrets``: fail fast
    (raise) in production, log a warning elsewhere so local/dev boots aren't blocked.

    INT-A1 sharpens this in two ways. It refuses a config that is *permissive*, not merely
    absent — asking only "is a non-dev origin pinned?" admitted the CORS spec's non-origin
    values, which are worse than the dev-defaults-only config this guard already refused.
    And ``ALLOWED_ORIGIN_REGEX`` is now refused outright in production rather than
    inspected: INT-09 already held that a regex alone never satisfies the explicit-origin
    requirement, so production enforces that instead of trying to judge the pattern. See
    ``_cors_problem``.
    """
    problem = _cors_problem(cfg)
    if problem is None:
        return
    if cfg.is_production:
        raise RuntimeError(problem)
    logger.warning("%s (allowed outside production)", problem)


def _on_schema_mismatch(message: str) -> None:
    """
    Handle a confirmed schema-vs-migrations mismatch.

    In production we fail fast (raise) so a stale schema can never silently
    serve traffic; elsewhere we log loudly so it surfaces in dev without
    blocking local/test boots.
    """
    logger.error(message)
    if settings.is_production:
        raise RuntimeError(message)


async def _check_alembic_head() -> None:
    """
    Lightweight check that the database is at the expected Alembic head.

    On a *confirmed* mismatch (DB reachable but not at head, or no migrations
    applied) this fails fast in production via ``_on_schema_mismatch``. Errors
    that merely mean we couldn't verify (connectivity, missing table on a fresh
    DB, test setups) stay non-fatal warnings so they don't block first boots.
    """
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            current_rev = result.scalar()

        # Compare against the latest revision in the migrations folder
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        alembic_cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(alembic_cfg)
        head_rev = script.get_current_head()
    except Exception as exc:
        # Outside production this is a non-fatal warning (fresh DB, connectivity not
        # ready, test setups). In production, an *inability to verify* the schema must
        # fail closed (INT-13) — the check exists precisely so we never serve traffic
        # against a possibly-stale schema, and swallowing the error defeats it.
        if settings.is_production:
            raise RuntimeError(
                f"Could not verify the database is at the Alembic head in production: {exc}"
            ) from exc
        logger.warning("Could not verify Alembic head (may be first run): %s", exc)
        return

    if not current_rev:
        _on_schema_mismatch(
            "No Alembic version applied to the database. "
            "Run `alembic upgrade head` before starting the app."
        )
    elif current_rev != head_rev:
        _on_schema_mismatch(
            "Database is not at Alembic head! "
            f"Current: {current_rev}, Expected head: {head_rev}. "
            "Run `alembic upgrade head` before starting the app."
        )
    else:
        logger.info("✅ Database at expected Alembic head (%s)", current_rev)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan.

    IMPORTANT: Schema management is handled exclusively by Alembic.
    Do NOT call Base.metadata.create_all here.

    Run `alembic upgrade head` (or your deployment migration step)
    before starting the application against a real database.
    """
    logger.info("🚀 Starting Performance Lab API (app.main:app v0.3.0)...")

    # Optional: lightweight connectivity check (does not mutate schema)
    try:
        async with engine.begin() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        logger.info("✅ Database connectivity verified")
    except Exception as exc:
        logger.warning("Database connectivity check failed (this may be expected in some test setups): %s", exc)

    # Critical safety check: production must not boot with a forgeable signing key
    _check_production_secrets(settings)

    # Critical safety check: production must pin an explicit CORS origin (no wildcard-subdomain default)
    _check_production_cors(settings)

    # Critical safety check: production must not log every SQL statement's bound parameters
    _check_production_debug(settings)

    # Critical safety check: ensure we're not running against a stale schema
    await _check_alembic_head()

    yield

    logger.info("🛑 Shutting down Performance Lab API...")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.3.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.include_router(onboard_router)
app.include_router(profile_router)
app.include_router(history_router)


# ----------------------------------------------------------------------
# Middleware
# ----------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_origin_regex=settings.allowed_origin_regex,  # disabled by default; pin explicit origins
    allow_credentials=True,           # Required for JWT Bearer tokens
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------
# Routers
# ----------------------------------------------------------------------

# Auth (no /v1 prefix — required for OAuth2 compatibility)
app.include_router(auth.router)

# Legacy v0.1 routes (used by frontend HeroFlowColumn)
app.include_router(legacy.router)

# Modern v1 API
app.include_router(ingest.router, prefix=settings.API_V1_STR)
app.include_router(prescribe.router, prefix=settings.API_V1_STR)
app.include_router(benchmarks.router, prefix=settings.API_V1_STR)
app.include_router(dashboard.router, prefix=settings.API_V1_STR)
app.include_router(planning.router, prefix=settings.API_V1_STR)
app.include_router(wellness.router, prefix=settings.API_V1_STR)
app.include_router(objectives.router, prefix=settings.API_V1_STR)
app.include_router(macrocycles.router, prefix=settings.API_V1_STR)
app.include_router(feedback.router, prefix=settings.API_V1_STR)
app.include_router(simulate.router, prefix=settings.API_V1_STR)
app.include_router(shadow.router, prefix=settings.API_V1_STR)
app.include_router(integrations_oura.router, prefix=settings.API_V1_STR)
app.include_router(weak_points.router, prefix=settings.API_V1_STR)
app.include_router(exercises.router, prefix=settings.API_V1_STR)


# ----------------------------------------------------------------------
# Health check
# ----------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    """Serve the React SPA shell at the root."""
    return FileResponse(os.path.join(_static_dir, "index.html"))


@app.get("/ping", tags=["Health"], response_model=None)
async def ping() -> dict[str, str]:
    """Simple health check endpoint."""
    return {
        "status": "ok",
        "system": "running",
        "version": "0.3.0",
        "project": settings.PROJECT_NAME,
    }


# ----------------------------------------------------------------------
# Global exception handler (optional but recommended)
# ----------------------------------------------------------------------

@app.exception_handler(CanonicalStateInvalid)
async def canonical_state_invalid_handler(
    request: Request, exc: CanonicalStateInvalid
) -> JSONResponse:
    """An intentional refusal: the athlete's canonical state cannot be trusted (INT-15 2B).

    409 rather than 4xx-as-client-error or 503: the request is well formed and resending it
    changes nothing — it conflicts with the persisted state of the athlete resource. The
    client cannot repair that; an operator or repair workflow must.

    Logged at WARNING with the internal reason: expected, but never routine. Zero of these
    is the target — each one is an athlete who cannot train today.
    """
    logger.warning(
        "canonical_state_invalid: capability=%s reason=%s path=%s",
        exc.capability,
        exc.normalized_reason,
        request.url.path,
    )
    return JSONResponse(status_code=409, content=exc.to_response_body())


@app.exception_handler(EngineStateDecodeError)
async def untranslated_engine_state_decode_error_handler(
    request: Request, exc: EngineStateDecodeError
) -> JSONResponse:
    """A raw codec failure reached the transport. That is a DEFECT, not a refusal.

    Deliberately an opaque 500, never a 409. A codec error escaping to HTTP means the
    authority-boundary translation was missed — an accidental strict call from the wrong
    surface, a missing display adapter, or a new route that forgot to translate. Turning it
    into a tidy 409 here would make every forgotten translation look like a working
    refusal, and hide the defect behind a plausible response.

    The codec knows the payload failed to decode. It does not know which capability is
    involved, so it cannot decide what the product owes the athlete. That decision belongs
    to the service that knows its own authority — see `app/core/errors.py`.
    """
    logger.error(
        "untranslated_engine_state_decode_error on %s %s: %s",
        request.method,
        request.url.path,
        type(exc).__name__,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Please try again later."},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log any unhandled exception (with traceback) and return clean JSON.

    The client gets a generic message (no internal detail leaked); the server
    keeps the full stack trace so 500s are diagnosable instead of vanishing.
    """
    logger.exception(
        "Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Please try again later."},
    )


# Optional: Add security headers middleware in the future

# ── Frontend static files ──────────────────────────────────────────────────────
# Vite outputs: index.html + assets/ (hashed JS/CSS chunks).
# /assets is served by StaticFiles (GET/HEAD only, no routing conflicts).
# SPA fallback (index.html for unknown GET paths) is handled via the 404
# exception handler below — no catch-all route that could shadow API endpoints.
_static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
_assets_dir = os.path.join(_static_dir, "assets")

if os.path.isdir(_assets_dir):
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static-root")


@app.exception_handler(StarletteHTTPException)
async def spa_404_handler(request: Request, exc: StarletteHTTPException) -> FileResponse | JSONResponse:
    """For GET 404s serve the SPA shell; all other errors return JSON."""
    if exc.status_code == 404 and request.method == "GET":
        index = os.path.join(_static_dir, "index.html")
        if os.path.isfile(index):
            return FileResponse(index)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
