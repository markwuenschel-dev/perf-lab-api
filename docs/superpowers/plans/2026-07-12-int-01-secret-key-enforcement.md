# Execution Contract: INT-01 — production boot must refuse a default/weak SECRET_KEY

**Status:** DELIVERED 2026-07-12 _(A1 lifespan guard; fork B handled — operator set a real SECRET_KEY in the prod env. Verified: 912 tests pass, ruff + pyright strict clean repo-wide, dev import smoke boots. Branch `feat/int-01-secret-key-enforcement`. PR open, not merged.)_

**Source:** candidate `INT-01` from the 2026-07-12 Repo Audit Swarm ledger (Bug/Security; severity 5; priority +16). The user selected the **SECRET_KEY** portion of the merged candidate.

---

## 1. Executive mission

A production boot with `SECRET_KEY` unset silently signs JWTs with the publicly-known default `"change-me-in-production"`, so anyone can forge `create_access_token` output and impersonate any user. Make production **refuse to boot** on a default, empty, or trivially-weak `SECRET_KEY` — the signing key must have no working default.

## 2. Current baseline

- **Branch/state:** `main` @ `0b9c70f`, clean. (INT-02 is a separate open PR #121 on its own branch; this mission branches fresh from `main` as `feat/int-01-secret-key-enforcement`.)
- **Runs today:** `ruff` clean, `pyright` strict `0/0/0`, full suite green. These tasks are **DB-free** (Settings + a boot function), so they verify without Postgres.
- **The defect (read this run):** `app/core/config.py:32` `SECRET_KEY: str = "change-me-in-production"`; consumed at `app/core/auth.py:58` (`jwt.encode(..., settings.SECRET_KEY, ...)`) and `:75` (`jwt.decode`). `settings.is_production` (`config.py:56`) is referenced **only** by the Alembic head check today.
- **Existing prod-fail-fast pattern:** `app/main.py:45-55` `_on_schema_mismatch` raises `RuntimeError` when `settings.is_production` else logs; `_check_alembic_head` (`:58-100`) is invoked from `lifespan` (`:123`). This is the established "fail fast in prod, log elsewhere" seam.
- **Pre-existing test that constrains the design:** `tests/test_startup_checks.py:10` constructs `Settings(ENVIRONMENT="production")` with **no** `SECRET_KEY` — an import/construction-time validator would break it (see §8 fork A).

## 3. Strategic meaning

The single highest-severity finding in the audit: a forgeable auth trust-root. The fix is small and contained but load-bearing — one boot guard closes full-account-impersonation. It also lights up `is_production` as a real security gate beyond the schema check.

## 4. Scope

- Extract the default key literal to a named constant so the guard and the field default cannot drift.
- A production boot guard that raises when `is_production` and `SECRET_KEY` is the default, empty/blank, or shorter than a 256-bit floor; logs a warning otherwise.
- Wire the guard into the app `lifespan`, alongside `_check_alembic_head`.
- Tests proving prod+weak raises, prod+strong passes, non-prod+weak only logs.

## 5. Non-goals

- **Not** the CORS `ALLOWED_ORIGIN_REGEX` railway default (needs the real prod origin — a separate decision; §20).
- **Not** `APP_ENCRYPTION_KEY` enforcement — it is opt-in by design (wearables only) and `app/core/crypto.py:30-46` already fails loudly on use; forcing it in prod would break wearable-free deploys (§20).
- **Not** rotating or generating keys, secret-manager integration, or `.env` tooling.
- **Not** the `int(user_id)` 401/500 issue (INT-10) even though it lives in `auth.py`.
- **Not** broad cleanup of `config.py` / `main.py`.

## 6. Blast-radius summary

Tiny and self-contained. No schema, API, generated artifact, or migration. Surfaces:
- `app/core/config.py` — the field default + a new module constant.
- `app/main.py` — the guard function + one `lifespan` call.
- `tests/` — one new test file.
- **Operational** (not code): production environments must carry a strong `SECRET_KEY` or the next boot fails — the intended fail-closed behavior (§8 fork B).
- CI unaffected: the guard runs in `lifespan` (not on import), and CI/import-smoke/OpenAPI-export run with `ENVIRONMENT=development`.

## 7. Contracts / seams involved

- **Config trust-root (owner: `app/core/config.py`):** `SECRET_KEY` field + `is_production` property.
- **Boot fail-fast (owner: `app/main.py` `lifespan`):** the `_on_schema_mismatch` / `_check_alembic_head` pattern the guard mirrors.

## 8. Human decisions required

**Fork A — enforcement point.**
- **A1 (recommended):** a `lifespan` startup guard `_check_production_secrets(cfg)` in `app/main.py`, mirroring `_check_alembic_head` — raises `RuntimeError` in production, logs otherwise. *Pros:* matches the established pattern; does not change `Settings` construction semantics; the existing `Settings(ENVIRONMENT="production")` test stays green; DB-free and unit-testable. *Con:* a script that imports `auth` without starting the ASGI app is not guarded (rare; the app boot is the real gate).
- **A2:** a pydantic `model_validator(mode="after")` on `Settings` — fails at construction, covering every entrypoint (scripts, alembic env, cron). *Pros:* strongest, no unguarded path. *Cons:* changes construction semantics repo-wide and **requires updating `tests/test_startup_checks.py:10`** to supply a `SECRET_KEY` for any production `Settings`; a heavier blast radius for a contained fix.
- _Assumed: A1. The task graph builds the lifespan guard._

**Fork B — production rollout coordination (migration/ops human-decision).** Enabling this guard will **block the next production boot if the live EC2 `SECRET_KEY` is unset or default** — which is the point, but must be coordinated. *Required action, not a code choice:* confirm a strong `SECRET_KEY` (≥32 chars, non-default) is set in the production environment as part of shipping this. Recommendation: verify/set the EC2 env var first, then merge. Surfaced here so it is never discovered at deploy time.

> The weak-key threshold itself (fail on default / empty / blank / `len < 32`) is a low-stakes technical default decided in §9, not a fork.

## 9. Implementation strategy

Decided shape (A1): a boot guard consistent with the existing schema check.

```
lifespan → _check_production_secrets(settings)
             is_production AND SECRET_KEY ∈ { DEFAULT_SECRET_KEY, "", blank, len<32 }  → raise RuntimeError
             not is_production AND weak                                                → logger.warning (dev convenience)
```

`DEFAULT_SECRET_KEY = "change-me-in-production"` becomes a named constant in `config.py`, used both as the field default and by the guard, so the two can never drift. The 32-char floor enforces a 256-bit key for HS256. Rejected alternative: the import-time `model_validator` (A2) — stronger but changes construction semantics and breaks an existing test for a one-guard fix.

## 10. Task graph

```
T1 (extract DEFAULT_SECRET_KEY constant)
  └─ T2 (production boot guard + lifespan wiring + tests)  depends T1
```

## 11. Task-by-task plan

### T1 — Name the default key constant
- **Depends:** none
- **Purpose:** one source of truth for the default literal so the guard and the field default cannot drift.
- **Files:** `app/core/config.py`
- **Action:** add module-level `DEFAULT_SECRET_KEY = "change-me-in-production"`; set `SECRET_KEY: str = DEFAULT_SECRET_KEY`. No behavior change.
- **Check:** existing `tests/test_startup_checks.py` still passes; `Settings().SECRET_KEY == DEFAULT_SECRET_KEY`.
- **Verify:** `uv run python -c "from app.core.config import Settings, DEFAULT_SECRET_KEY; assert Settings().SECRET_KEY == DEFAULT_SECRET_KEY"` exits 0; `uv run pytest tests/test_startup_checks.py -q` green; `uv run ruff check app/core/config.py && uv run pyright app/core/config.py` clean.
- **Risk/rollback:** trivial; revert the two lines.

### T2 — Production boot guard + lifespan wiring
- **Depends:** T1
- **Purpose:** refuse to boot in production with a default/empty/weak signing key.
- **Files:** `app/main.py`, `tests/test_secret_key_enforcement.py` `NEW`
- **Action:** add `_check_production_secrets(cfg: Settings) -> None` — if `cfg.is_production` and `cfg.SECRET_KEY.strip()` is `""`, equals `DEFAULT_SECRET_KEY`, or `len(cfg.SECRET_KEY.strip()) < 32` → `raise RuntimeError("SECRET_KEY must be set to a strong value in production …")`; otherwise if the key is weak, `logger.warning(...)`. Call `_check_production_secrets(settings)` in `lifespan` (before `_check_alembic_head`).
- **Check:** `tests/test_secret_key_enforcement.py` — (a) `Settings(ENVIRONMENT="production", SECRET_KEY=DEFAULT_SECRET_KEY)` → raises; (b) empty and short (`"x"*8`) prod keys → raise; (c) `Settings(ENVIRONMENT="production", SECRET_KEY="x"*40)` → passes; (d) `Settings(ENVIRONMENT="development")` default → does not raise.
- **Verify:** `uv run pytest tests/test_secret_key_enforcement.py -q` green; `uv run ruff check app/main.py && uv run pyright app/main.py` clean; import-smoke `uv run python -c "import app.main"` exits 0 (dev env → no raise).
- **Risk/rollback:** if the live prod key is weak, the next prod boot fails (intended — see §8 fork B); rollback = remove the `lifespan` call. Guard runs only at startup, so import/CI paths are unaffected.

## 12. Execution mode

**Sequential.** No contract, schema, public API, generated artifact, fixture, or cross-language seam changes — a contained two-file boot guard plus tests. One agent works T1 then T2 in order. (Ledger `execution_mode` for INT-01 was `sequential`.)

## 13. Required commands

```bash
uv run pytest tests/test_secret_key_enforcement.py tests/test_startup_checks.py -q
uv run python -c "import app.main"     # dev-env import smoke: must not raise
uv run ruff check .
uv run pyright
```

## 14. Verification gates

- **After T1:** `test_startup_checks.py` green; the constant round-trips.
- **After T2:** the new enforcement tests green (prod+weak raises, prod+strong passes, dev never raises); import-smoke exits 0; `ruff`/`pyright` clean.
- **Final:** full `uv run pytest -q` green (no collateral), `ruff`/`pyright` clean.

## 15. Failure codes

```
FAIL-SCOPE-CREEP        — touched CORS regex, APP_ENCRYPTION_KEY, or INT-10 int(user_id).
FAIL-PHANTOM-TARGET     — named a file absent from baseline and not marked NEW.
FAIL-UNVERIFIED-TASK    — reported done without the verify command output.
FAIL-FAKE-GREEN         — the guard is present but never wired into lifespan (dead check).
FAIL-DEV-BOOT-BLOCKED   — a non-production boot raises (the guard must only warn outside prod).
FAIL-DRIFTED-DEFAULT    — the guard compares against a hardcoded literal instead of DEFAULT_SECRET_KEY.
```

## 16. Negative fixtures

- `Settings(ENVIRONMENT="production", SECRET_KEY="change-me-in-production")` → `_check_production_secrets` raises `RuntimeError`.
- `Settings(ENVIRONMENT="production", SECRET_KEY="")` and `SECRET_KEY="x"*8` → raise (empty and sub-256-bit).
- `Settings(ENVIRONMENT="development")` with the default key → does **not** raise (warns only).

## 17. Review plan

- **Spec axis:** production refuses default/empty/short keys; non-production only warns; the guard is actually called in `lifespan`; the default literal is a single named constant.
- **Quality axis:** mirrors the existing `_on_schema_mismatch` pattern; no widening of `config`/`main` interfaces; the guard is a pure function of its `Settings` arg (unit-testable without the global); message tells the operator exactly what to set.

## 18. Merge gate

Open the PR when: the §13 commands are green, full `uv run pytest -q` green, `ruff`/`pyright` clean, and the PR body records the §8-fork-B operational action (confirm a strong prod `SECRET_KEY` is set before merge/deploy). **Open PR and stop — do not merge.**

## 19. Definition of done

1. `uv run pytest tests/test_secret_key_enforcement.py -q` → all green (prod default/empty/short raise; prod strong passes; dev never raises).
2. `uv run pytest tests/test_startup_checks.py -q` → green (unchanged behavior).
3. `uv run python -c "import app.main"` → exits 0 (dev import unaffected).
4. `uv run ruff check . && uv run pyright` → clean.

## 20. Follow-ups

- **CORS `ALLOWED_ORIGIN_REGEX`** (sibling INT-01 part): replace the `railway.app` default with the real prod origin (`perflab.44-198-76-44.nip.io`) / `""` + require explicit prod env — needs the origin decision; pairs with the deploy-topology-drift candidate INT-09.
- **`APP_ENCRYPTION_KEY`**: optionally require it in prod *only when wearable sync is enabled*, or a startup warning — separate from the signing-key trust-root.
- **A2 (model_validator)**: if an unguarded script path ever matters, promote enforcement to construction time (with the `test_startup_checks.py` update).
- **INT-10** (`int(user_id)` → 401 not 500) and **INT-13** (`_check_alembic_head` fail-open) are adjacent `auth.py`/`main.py` findings in the ledger — separate loops.
