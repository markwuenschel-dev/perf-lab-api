"""Guard: the two domain-vector import paths resolve to one set of classes (INT-11).

Domain vectors are importable both from the canonical ``app.domain.vectors`` and
from the backward-compat shim ``app.schemas.engine_vectors``. That is fine *only*
while the shim stays a pure re-export — if someone re-defines a class in the shim,
the two paths silently become two different types and isinstance / model checks
across the engine start failing in confusing ways. This test fails the moment the
shim stops being identity-equal to the domain layer.
"""
import pathlib

import app.domain.vectors as domain
import app.schemas.engine_vectors as shim

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
# Layers that are "internal engine code" per the shim's own docstring: they must import
# domain vectors from the canonical app.domain.vectors, never the backward-compat shim.
_CANONICAL_ONLY_LAYERS = ("app/engine", "app/logic")


def test_shim_reexports_are_identical_objects():
    shared = [name for name in shim.__all__ if hasattr(domain, name)]
    assert shared, "engine_vectors.__all__ should re-export domain names"
    mismatches = [
        name for name in shared if getattr(shim, name) is not getattr(domain, name)
    ]
    assert not mismatches, (
        "app.schemas.engine_vectors must stay a pure re-export of app.domain.vectors "
        f"— these names diverged into separate objects: {mismatches}"
    )


def test_shim_exposes_no_extra_vector_classes():
    """Every name the shim advertises must come from the domain layer."""
    missing = [name for name in shim.__all__ if not hasattr(domain, name)]
    assert not missing, (
        f"engine_vectors re-exports names absent from app.domain.vectors: {missing}"
    )


def test_engine_and_logic_import_vectors_from_the_domain_layer():
    """Internal engine code imports vectors from the canonical path, not the shim (PA-26).

    The shim exists for backward-compat at the outer layers; app/engine and app/logic are
    the model core and should point at app.domain.vectors directly. This fails the moment
    a new ``from app.schemas.engine_vectors import`` (or bare import) lands under either.
    """
    offenders: list[str] = []
    for layer in _CANONICAL_ONLY_LAYERS:
        for path in sorted((_REPO_ROOT / layer).rglob("*.py")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(
                    ("from app.schemas.engine_vectors import", "import app.schemas.engine_vectors")
                ):
                    offenders.append(f"{path.relative_to(_REPO_ROOT).as_posix()}:{lineno}")
    assert not offenders, (
        "engine/logic code must import domain vectors from app.domain.vectors, not the "
        f"backward-compat shim app.schemas.engine_vectors — offenders: {offenders}"
    )
