# tests/test_models_have_no_pydantic_fields.py
"""Fitness check: SQLAlchemy models must not declare pydantic ``Field`` attributes.

A ``pydantic.Field(...)`` assigned on a SQLAlchemy declarative class is silently
inert. It creates no column, so nothing is persisted or queryable, and every
instance carries the raw ``FieldInfo`` sentinel under that name rather than a
value of the annotated type::

    class AthleteState(Base):
        recent_damage: float = Field(default=0.0)   # never a float at runtime

Type checkers do not catch this — the annotation is locally consistent and it is
the ORM semantics that are wrong — so it is asserted structurally here instead.
Model attributes must be declared with ``mapped_column`` / ``relationship``; use
pydantic only in API schemas, never on a declarative model.
"""

import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest
from pydantic.fields import FieldInfo

import app.models
from app.core.db import Base

MODELS_PACKAGE = "app.models"


def _model_modules() -> list[str]:
    """Every module under ``app/models/``, imported by dotted name."""
    package_dir = Path(app.models.__file__).parent
    return sorted(
        f"{MODELS_PACKAGE}.{info.name}"
        for info in pkgutil.iter_modules([str(package_dir)])
        if not info.ispkg
    )


def _declarative_classes(module_name: str) -> list[type]:
    """Declarative model classes *defined in* this module (not imported into it)."""
    module = importlib.import_module(module_name)
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Base) and obj is not Base and obj.__module__ == module_name
    ]


def test_model_modules_are_discovered() -> None:
    """Guard the guard: an empty walk would make the real test vacuously green."""
    modules = _model_modules()
    assert modules, "no modules discovered under app/models/"
    assert any(_declarative_classes(m) for m in modules), (
        "no declarative classes discovered under app/models/ — "
        "the pydantic-field check would pass vacuously"
    )


@pytest.mark.parametrize("module_name", _model_modules())
def test_no_pydantic_field_attributes_on_models(module_name: str) -> None:
    """No declarative model may carry a pydantic ``FieldInfo`` class attribute."""
    offenders: list[str] = []

    for model in _declarative_classes(module_name):
        # Walk the declared namespace of the class and its model bases. __dict__
        # is deliberate: a FieldInfo left on the class is never turned into a
        # descriptor by the mapper, so it survives verbatim and getattr() on the
        # class would just hand back the same sentinel.
        for klass in model.__mro__:
            if klass is Base or not issubclass(klass, Base):
                continue
            for attr_name, value in vars(klass).items():
                if isinstance(value, FieldInfo):
                    offenders.append(f"{klass.__module__}.{klass.__name__}.{attr_name}")

    assert not offenders, (
        "pydantic Field(...) declared on SQLAlchemy model(s): "
        + ", ".join(sorted(set(offenders)))
        + ". These create no column and are FieldInfo at runtime, not the "
        "annotated type. Use mapped_column()/relationship(), or move the field "
        "to an API schema."
    )
