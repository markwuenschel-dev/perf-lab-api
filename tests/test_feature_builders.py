"""Smoke/interface tests for feature builders — no DB required.

Verifies:
  (a) Every builder module is importable and exposes a callable build_dataset.
  (b) build_dataset is a coroutine function in each builder.
  (c) The export script's BUILDERS dict has all 10 entries and every mapped
      module path is importable and exposes build_dataset.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_export_script() -> object:
    """Load scripts/export_validation_datasets.py by file path (no __init__.py)."""
    script_path = _REPO_ROOT / "scripts" / "export_validation_datasets.py"
    spec = importlib.util.spec_from_file_location(
        "export_validation_datasets", script_path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

BUILDER_MODULES = [
    "app.analysis.feature_builders.session_decrement",
    "app.analysis.feature_builders.fatigue_recovery",
    "app.analysis.feature_builders.tissue_risk_features",
    "app.analysis.feature_builders.sleep_stress_residual",
    "app.analysis.feature_builders.benchmark_validity_features",
    "app.analysis.feature_builders.deload_risk_features",
    "app.analysis.feature_builders.experiment_features",
    "app.analysis.feature_builders.scoring_weight_features",
    "app.analysis.feature_builders.interference_features",
    "app.analysis.feature_builders.confidence_calibration_features",
]


@pytest.mark.parametrize("module_path", BUILDER_MODULES)
def test_builder_importable(module_path: str) -> None:
    mod = importlib.import_module(module_path)
    assert callable(getattr(mod, "build_dataset", None)), (
        f"{module_path} must expose a callable build_dataset"
    )


@pytest.mark.parametrize("module_path", BUILDER_MODULES)
def test_builder_is_coroutine(module_path: str) -> None:
    mod = importlib.import_module(module_path)
    assert inspect.iscoroutinefunction(mod.build_dataset), (
        f"{module_path}.build_dataset must be a coroutine function (async def)"
    )


def test_export_script_builders_count() -> None:
    script = _load_export_script()
    builders = script.BUILDERS  # type: ignore[attr-defined]
    assert len(builders) == 10, f"Expected 10 builders, got {len(builders)}"


def test_export_script_builders_all_importable() -> None:
    script = _load_export_script()
    builders = script.BUILDERS  # type: ignore[attr-defined]
    for name, module_path in builders.items():
        mod = importlib.import_module(module_path)
        assert callable(getattr(mod, "build_dataset", None)), (
            f"BUILDERS[{name!r}] → {module_path} must expose callable build_dataset"
        )


def test_export_script_builders_all_coroutines() -> None:
    script = _load_export_script()
    builders = script.BUILDERS  # type: ignore[attr-defined]
    for name, module_path in builders.items():
        mod = importlib.import_module(module_path)
        assert inspect.iscoroutinefunction(mod.build_dataset), (
            f"BUILDERS[{name!r}] → {module_path}.build_dataset must be async"
        )


def test_export_script_builders_no_drift() -> None:
    """BUILDERS registry must match the canonical module list exactly."""
    script = _load_export_script()
    builders = script.BUILDERS  # type: ignore[attr-defined]
    registry_paths = set(builders.values())
    canonical_paths = set(BUILDER_MODULES)
    assert registry_paths == canonical_paths, (
        f"Drift detected.\n"
        f"  In registry but not canonical: {registry_paths - canonical_paths}\n"
        f"  In canonical but not registry: {canonical_paths - registry_paths}"
    )
