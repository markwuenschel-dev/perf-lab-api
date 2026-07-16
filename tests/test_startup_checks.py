"""Startup Alembic-head fail-fast behavior (§1.1)."""

import pytest
from conftest import assert_does_not_raise

import app.main as main_module
from app.core.config import Settings


def test_is_production_parsing():
    assert Settings(ENVIRONMENT="production").is_production is True
    assert Settings(ENVIRONMENT="Prod").is_production is True
    assert Settings(ENVIRONMENT="development").is_production is False
    assert Settings(ENVIRONMENT="").is_production is False


def test_schema_mismatch_raises_in_production(monkeypatch):
    monkeypatch.setattr(main_module.settings, "ENVIRONMENT", "production")
    with pytest.raises(RuntimeError):
        main_module._on_schema_mismatch("schema is behind head")


def test_schema_mismatch_only_logs_outside_production(monkeypatch):
    monkeypatch.setattr(main_module.settings, "ENVIRONMENT", "development")
    # Must not raise in non-production environments.
    with assert_does_not_raise():
        main_module._on_schema_mismatch("schema is behind head")
