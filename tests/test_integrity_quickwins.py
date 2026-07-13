"""DB-free unit tests for the INT quick-wins cluster (INT-04/13/26/31).

Each targets a specific silent-failure / fail-open finding from the 2026-07-12
Repo Audit Swarm ledger; none needs Postgres.
"""
import logging
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.core import crypto
from app.domain.vectors import CapacityState
from app.engine.parameters import default_parameters
from app.logic import state_update_v0

# asyncio_mode="auto" (pyproject) runs the async tests without an explicit marker.


@contextmanager
def capture_logs(logger_name: str, level: int = logging.WARNING):
    """Capture records from one named logger, immune to global logging state.

    ``caplog`` relies on propagation to a root handler, which other tests in the
    full suite can perturb (levels, ``logging.disable``, propagation). Attaching a
    handler directly to the target logger — and forcing it enabled for the block —
    makes these assertions order-independent.
    """
    logger = logging.getLogger(logger_name)
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.setLevel(level)
    handler.emit = records.append  # type: ignore[method-assign]

    prev_disabled_root = logging.root.manager.disable
    prev = (logger.level, logger.disabled)
    logging.disable(logging.NOTSET)
    logger.setLevel(level)
    logger.disabled = False
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev[0])
        logger.disabled = prev[1]
        logging.disable(prev_disabled_root)


def _text(records: list[logging.LogRecord]) -> str:
    return "\n".join(r.getMessage() for r in records)


# --- INT-31: Fernet cache keyed by value, not pinned for process lifetime ---------

def test_fernet_rebuilds_when_key_rotates(monkeypatch):
    crypto._fernet_for.cache_clear()
    k1 = Fernet.generate_key().decode()
    k2 = Fernet.generate_key().decode()

    monkeypatch.setattr(crypto.settings, "APP_ENCRYPTION_KEY", k1)
    f1 = crypto._fernet()
    monkeypatch.setattr(crypto.settings, "APP_ENCRYPTION_KEY", k2)
    f2 = crypto._fernet()

    assert f1 is not f2, "rotated key must yield a fresh Fernet, not the pinned first one"
    # And the current key actually round-trips.
    assert crypto.decrypt(crypto.encrypt("secret")) == "secret"
    crypto._fernet_for.cache_clear()


def test_fernet_same_key_is_cached(monkeypatch):
    crypto._fernet_for.cache_clear()
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto.settings, "APP_ENCRYPTION_KEY", key)
    assert crypto._fernet() is crypto._fernet()
    crypto._fernet_for.cache_clear()


# --- INT-26: unknown capacity target_key logs instead of silently no-op'ing -------

def test_unknown_capacity_target_key_warns():
    # _apply_capacity_residual reads only `state.capacity_x`; a namespace suffices.
    state = SimpleNamespace(capacity_x=CapacityState())
    mapping = SimpleNamespace(target_key="not_a_real_axis", id=42, config={}, coefficient=1.0)
    with capture_logs("app.logic.state_update_v0", logging.WARNING) as records:
        # Must not raise; just skip the correction and log it.
        state_update_v0._apply_capacity_residual(
            state, mapping, 0.8, 1.0, default_parameters(), None
        )
    text = _text(records)
    assert "unknown target_key" in text
    assert "not_a_real_axis" in text


# --- INT-04: seed_catalog escalates failures + flags an empty catalog -------------

async def test_seed_catalog_flags_empty_catalog(monkeypatch):
    from app.scripts import seed_catalog

    async def _ok() -> None:
        return None

    async def _zero() -> int:
        return 0

    monkeypatch.setattr(seed_catalog.seed_exercises, "seed", _ok)
    monkeypatch.setattr(seed_catalog.seed_benchmarks, "seed", _ok)
    monkeypatch.setattr(seed_catalog, "_benchmark_definition_count", _zero)

    with capture_logs("app.scripts.seed_catalog", logging.ERROR) as records:
        await seed_catalog.seed_catalog()
    assert "EMPTY" in _text(records)


async def test_seed_catalog_step_failure_is_loud_not_silent(monkeypatch):
    from app.scripts import seed_catalog

    async def _boom() -> None:
        raise RuntimeError("seed exploded")

    async def _ok() -> None:
        return None

    async def _some() -> int:
        return 7

    monkeypatch.setattr(seed_catalog.seed_exercises, "seed", _boom)
    monkeypatch.setattr(seed_catalog.seed_benchmarks, "seed", _ok)
    monkeypatch.setattr(seed_catalog, "_benchmark_definition_count", _some)

    with capture_logs("app.scripts.seed_catalog", logging.ERROR) as records:
        # A failing step must be swallowed (boot continues) but logged at ERROR.
        await seed_catalog.seed_catalog()
    assert "FAILED" in _text(records)


# --- INT-13: Alembic head check fails closed in production ------------------------

class _BoomEngine:
    """Stand-in engine whose connect() fails, simulating an unverifiable schema."""

    def connect(self):  # noqa: ANN201 - test stub
        raise OSError("db not reachable")


async def test_alembic_head_check_fails_closed_in_production(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "engine", _BoomEngine())
    monkeypatch.setattr(main.settings, "ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="Alembic head in production"):
        await main._check_alembic_head()


async def test_alembic_head_check_warns_but_boots_outside_production(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "engine", _BoomEngine())
    monkeypatch.setattr(main.settings, "ENVIRONMENT", "development")
    with capture_logs("perflab", logging.WARNING) as records:
        await main._check_alembic_head()  # must NOT raise
    assert "Could not verify Alembic head" in _text(records)
