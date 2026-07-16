"""Fitness check: every model file must be imported by app/models/__init__.py.

Alembic autogenerate and the app discover tables through ``Base.metadata``, which is
populated only when a model class is actually imported. ``app/models/__init__.py`` is
the hand-maintained list of imports that does exactly this. Nothing ties that list to
the filesystem — so a new ``app/models/foo.py`` added *without* a matching line in
``__init__.py`` registers no table on ``Base.metadata``. Alembic then silently emits no
migration for it, and the table ships missing. (The pydantic-field check walks the
filesystem directly, so it would pass even in that state — it does not cover this.)

This test walks ``app/models/`` on disk, imports each module independently, and asserts
every declarative class it finds is also reachable from the ``app.models`` package
namespace — i.e. is imported by ``__init__.py``. It passes today; it fails the moment a
model file is added but not registered.
"""
import importlib
import inspect
import pkgutil
from pathlib import Path

import app.models
from app.core.db import Base

MODELS_PACKAGE = "app.models"


def _model_modules() -> list[str]:
    """Every non-package module under ``app/models/``, by dotted name."""
    package_dir = Path(app.models.__file__).parent
    return sorted(
        f"{MODELS_PACKAGE}.{info.name}"
        for info in pkgutil.iter_modules([str(package_dir)])
        if not info.ispkg
    )


def _mapped_classes(module_name: str) -> list[type]:
    """Mapped declarative classes *defined in* this module (skip imported + abstract)."""
    module = importlib.import_module(module_name)
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Base)
        and obj is not Base
        and obj.__module__ == module_name
        and hasattr(obj, "__table__")  # concrete mapped table, not an abstract base
    ]


def test_model_modules_are_discovered() -> None:
    """Guard the guard: an empty walk would make the registration check vacuously green."""
    modules = _model_modules()
    assert modules, "no modules discovered under app/models/"
    assert any(_mapped_classes(m) for m in modules), (
        "no mapped classes discovered under app/models/ — the registration check "
        "would pass vacuously"
    )


def test_every_model_is_registered_in_package_namespace() -> None:
    """Every mapped class on disk must be imported by app/models/__init__.py."""
    unregistered: list[str] = []
    for module_name in _model_modules():
        for model in _mapped_classes(module_name):
            if getattr(app.models, model.__name__, None) is not model:
                unregistered.append(f"{module_name}.{model.__name__}")

    assert not unregistered, (
        "model class(es) defined on disk but not imported by app/models/__init__.py: "
        + ", ".join(sorted(unregistered))
        + ". Unimported models never register on Base.metadata, so Alembic autogenerate "
        "cannot see them and the table ships missing. Add the import to "
        "app/models/__init__.py."
    )
