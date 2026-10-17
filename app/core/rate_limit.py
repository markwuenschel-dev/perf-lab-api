"""
app/core/rate_limit.py

Per-IP rate limits on the unauthenticated credential routes (P9, ADR-0076).

Each uvicorn worker keeps its own counters in memory (`limits` MemoryStorage, moving
window), so a stated rate is per IP *per worker*. With the production image's two workers,
one IP gets at most twice the rate in any rolling window — while both workers stay up, the
counters stay healthy and nothing restarts. A restart or deploy empties every counter. This
reduces repeated attempts from one address; it is not protection against a distributed
flood.

The key is `request.client.host`, which uvicorn has already normalized: behind Caddy,
`ProxyHeadersMiddleware` replaces it with the forwarded client only when the connection
comes from the address in FORWARDED_ALLOW_IPS (`app.main._check_production_proxy_trust`).
Nothing here reads a forwarding header itself.

Why `limits` directly and not slowapi's decorator: the decorator needs a `Response`
parameter to inject headers, and when its counter fails it swallows the error and then
raises `AttributeError: view_rate_limit` after the route body has run — a committed
registration answered with a 500. It also reads RATELIMIT_* settings from the environment
and a `.env` file, any of which can disable enforcement. This module reads no
configuration: the rates and the strategy below are the configuration.
"""

from __future__ import annotations

import logging
import math
import time

from fastapi import HTTPException, Request
from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter, RateLimiter

logger = logging.getLogger("perflab")

# The approved starting rates (2026-10-17), per IP per worker, each route its own bucket.
LOGIN_RATE: RateLimitItem = parse("10/minute")
REGISTER_RATE: RateLimitItem = parse("5/hour")

# The body of every 429. Generic: it says nothing about the account or the counter.
TOO_MANY_REQUESTS = "Too many requests"


class AuthRateLimiter:
    """One worker's counters: a moving window over in-memory storage."""

    def __init__(
        self,
        storage: MemoryStorage | None = None,
        strategy: type[RateLimiter] = MovingWindowRateLimiter,
    ) -> None:
        self.storage = storage if storage is not None else MemoryStorage()
        self.strategy: RateLimiter = strategy(self.storage)

    def hit(self, rule: RateLimitItem, scope: str, client: str) -> bool:
        """Count one request; False when it is over the rule's rate."""
        return self.strategy.hit(rule, scope, client)

    def retry_after(self, rule: RateLimitItem, scope: str, client: str) -> int:
        """Whole seconds until the window admits another request (at least 1)."""
        try:
            reset_at = self.strategy.get_window_stats(rule, scope, client).reset_time
            return max(1, math.ceil(reset_at - time.time()))
        except Exception:
            return rule.get_expiry()

    def reset(self) -> None:
        self.storage.reset()


# This worker's limiter. Tests reset it between cases (tests/conftest.py).
auth_limiter = AuthRateLimiter()


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def rate_limited(scope: str, rule: RateLimitItem):
    """A route dependency that counts the request before the route body runs.

    Over the rate: 429 with `Retry-After`, and the body (credential lookup, bcrypt) never
    runs. Counter failure: logged at ERROR and the request proceeds as if unlimited, so a
    broken counter never turns a valid login or registration into an error (fail open).
    """

    async def check(request: Request) -> None:
        client = _client_key(request)
        try:
            allowed = auth_limiter.hit(rule, scope, client)
        except Exception:
            logger.error(
                "Rate limit counter failed for %s; allowing the request", scope, exc_info=True
            )
            return
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=TOO_MANY_REQUESTS,
                headers={"Retry-After": str(auth_limiter.retry_after(rule, scope, client))},
            )

    return check
