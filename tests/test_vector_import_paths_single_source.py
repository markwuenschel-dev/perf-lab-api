"""Guard: the two domain-vector import paths resolve to one set of classes (INT-11).

Domain vectors are importable both from the canonical ``app.domain.vectors`` and
from the backward-compat shim ``app.schemas.engine_vectors``. That is fine *only*
while the shim stays a pure re-export — if someone re-defines a class in the shim,
the two paths silently become two different types and isinstance / model checks
across the engine start failing in confusing ways. This test fails the moment the
shim stops being identity-equal to the domain layer.
"""
import app.domain.vectors as domain
import app.schemas.engine_vectors as shim


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
