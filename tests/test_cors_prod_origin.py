"""DB-free unit tests for INT-09: production CORS pins an explicit prod origin.

Mirrors the INT-01 SECRET_KEY fail-closed pattern — production must refuse to boot
unless an explicit (non-dev-default) CORS origin is pinned. None of these need Postgres.
"""
import logging
from contextlib import contextmanager

import pytest

from app.core.config import DEV_DEFAULT_ORIGINS, Settings

PROD_ORIGIN = "https://perflab.44-198-76-44.nip.io"


@contextmanager
def caplog_at(logger_name: str, level: int = logging.WARNING):
    """Capture records from one named logger, immune to global logging state."""
    logger = logging.getLogger(logger_name)
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.setLevel(level)
    handler.emit = records.append  # type: ignore[method-assign]

    prev = (logger.level, logger.disabled)
    prev_disabled_root = logging.root.manager.disable
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


def _settings(**overrides) -> Settings:
    """Build a Settings instance with explicit CORS-relevant fields (ignores real env)."""
    base = {
        "ENVIRONMENT": "development",
        "ALLOWED_ORIGINS": ",".join(DEV_DEFAULT_ORIGINS),
        "ALLOWED_ORIGIN_REGEX": "",
        "_env_file": None,  # don't read a local .env during tests
    }
    base.update(overrides)
    return Settings(**base)


def test_prod_with_only_dev_defaults_and_empty_regex_raises():
    from app import main

    cfg = _settings(
        ENVIRONMENT="production",
        ALLOWED_ORIGINS=",".join(DEV_DEFAULT_ORIGINS),
        ALLOWED_ORIGIN_REGEX="",
    )
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS=https://perflab.44-198-76-44.nip.io"):
        main._check_production_cors(cfg)


def test_prod_with_explicit_pinned_origin_does_not_raise():
    from app import main

    cfg = _settings(
        ENVIRONMENT="production",
        ALLOWED_ORIGINS=",".join((*DEV_DEFAULT_ORIGINS, PROD_ORIGIN)),
        ALLOWED_ORIGIN_REGEX="",
    )
    # Must not raise, and the pinned origin must actually be in the allowlist.
    main._check_production_cors(cfg)
    assert PROD_ORIGIN in cfg.allowed_origins_list


def test_development_with_dev_defaults_does_not_raise():
    from app import main

    cfg = _settings(ENVIRONMENT="development")
    # Non-production only warns; it must never raise.
    with caplog_at("perflab", logging.WARNING) as records:
        main._check_production_cors(cfg)
    assert any("explicit production CORS origin" in r.getMessage() for r in records)


def test_config_default_regex_removed_and_property_none():
    cfg = _settings()
    # The old wildcard-subdomain regex default is gone: empty string, property collapses to None.
    assert cfg.ALLOWED_ORIGIN_REGEX == ""
    assert cfg.allowed_origin_regex is None
