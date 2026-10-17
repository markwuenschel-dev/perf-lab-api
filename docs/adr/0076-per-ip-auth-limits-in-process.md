---
status: accepted
date: 2026-10-17
---
# Login and registration are limited per IP, in process, and login always verifies once

## Context

Nothing limited `POST /auth/token` or `POST /auth/register`. Login returned before any bcrypt
work when the email had no account (`not user or not verify_password(...)`), so a failed login
was fast for an unknown email and slow (a cost-12 hash) for a known one. slowapi sat in the
optional `tasks` extra, which the image never installs. Behind Caddy, uvicorn trusted forwarded
headers only from 127.0.0.1, so `request.client.host` was Caddy's address for every request.
ADR-0027 keeps Redis out of the default runtime.

## Decision

1. **Per-IP limits, per worker, in memory** (`app/core/rate_limit.py`). `POST /auth/token`
   10/minute, `POST /auth/register` 5/hour, each route its own bucket, keyed on
   `request.client.host`. A moving window over `limits.MemoryStorage` in each uvicorn worker.
   With the image's two workers one IP gets at most 20 logins in any rolling 60 s and 10
   registrations in any rolling 60 min, while both workers stay up, their counters stay
   healthy and nothing restarts. A restart or deploy empties the counters. Fixed windows were
   not used: they admit a second full burst at the window boundary.
2. **The check runs before the route body**, as a route dependency: an over-rate request
   gets a 429 with `Retry-After` and never reaches the credential lookup or bcrypt.
3. **A broken counter fails open over the whole response path.** The error is logged at
   ERROR and the request proceeds; a valid login still gets its token, a registration still
   commits once and returns 201, invalid credentials still get 401.
4. **`limits` directly, not slowapi's decorator.** In slowapi 0.1.10 the decorator needs a
   `Response` parameter to inject headers, and when its counter fails it swallows the error
   and then raises `AttributeError: view_rate_limit` after the route body has run. It also
   reads `RATELIMIT_*` settings from the environment and from a `.env` file, any of which can
   disable enforcement. The limiter here reads no configuration.
5. **The client address comes from uvicorn**, never from a header read in app code. Caddy
   ignores client-sent `X-Forwarded-*`; uvicorn replaces `request.client` with the forwarded
   client only for connections from `FORWARDED_ALLOW_IPS`. Production refuses to start unless
   that is exactly one specific IP address — Caddy's fixed address on a network it alone
   shares with the API (`app.main._check_production_proxy_trust`). Empty, `*`, a network, a
   list or a malformed value is a deployment error.
6. **Login always runs exactly one bcrypt check**: against the account's hash, or against a
   fixed committed dummy hash at the same cost when there is no account (OWASP: no quick-exit
   timing difference). A missing account always fails, even if the password matches the dummy.

## Consequences

- This reduces repeated attempts from one address. It does not protect against a distributed
  flood, whichever store holds the counters.
- Registration still answers 409 for a registered email, by design (INT-A8), so whether an
  account exists remains discoverable, at the registration rate. Removing that needs a
  non-revealing registration flow with email verification, which is out of scope.
- The global HTTP exception handler now forwards an exception's headers, so `Retry-After` and
  login's `WWW-Authenticate: Bearer` reach clients (it dropped them before).
- slowapi stays in the unused `tasks` extra; `limits` is a base dependency.
