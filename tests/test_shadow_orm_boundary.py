"""AUD-C24: display-telemetry shadow modules must not touch the WellnessSample ORM entity.

The recovery + personalization shadow writers (and their shared telemetry calc module) must
receive immutable inputs — a ``WellnessTelemetrySnapshot`` for the current observation and
``WellnessHistoryPoint`` projections for history — never a live ``WellnessSample`` an earlier
shadow's rollback could expire (the production-real cascade). This guard forbids those modules
from importing or referencing the ORM entity at all, so the boundary is mechanically enforced,
not maintained by review memory. The repository implementation remains the sole approved home
for the query. AST-based: a mention in a docstring is prose, not a reference, and is allowed.
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# Production modules that must stay free of the WellnessSample ORM dependency.
GUARDED = [
    APP / "services" / "recovery_shadow_service.py",
    APP / "services" / "personalization_shadow_service.py",
    APP / "logic" / "recovery_telemetry.py",  # the shared calculation module
]


def _wellness_sample_refs(path: Path) -> list[int]:
    """Line numbers where ``WellnessSample`` is referenced in code (name / attribute / import)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "WellnessSample":
            hits.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "WellnessSample":
            hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            hits.extend(node.lineno for alias in node.names if alias.name == "WellnessSample")
    return hits


def test_guarded_modules_exist() -> None:
    """Guard the guard: a path typo must not let the check pass vacuously."""
    for path in GUARDED:
        assert path.is_file(), f"guarded module missing: {path}"


def test_shadow_telemetry_modules_do_not_reference_wellness_sample() -> None:
    offenders = {p.name: hits for p in GUARDED if (hits := _wellness_sample_refs(p))}
    assert not offenders, (
        "AUD-C24: display-telemetry shadow modules must take immutable snapshots/projections, "
        f"not the WellnessSample ORM entity: {offenders}. Route the read through "
        "AthleteContextRepository (list_wellness_history) / the WellnessTelemetrySnapshot boundary."
    )
