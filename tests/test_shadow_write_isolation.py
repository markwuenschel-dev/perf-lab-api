"""Fitness gate: shadow-write services delegate their commit/rollback to one seam.

Every ``app/services/*_shadow_service.py`` is a best-effort side-channel writer whose
failure must never break the request that triggered it. ``telemetry_common.best_effort_write``
owns that "commit; on failure log-and-rollback" dance in exactly one place — including the
guard against a rollback that itself raises. ``dose_routing_shadow_service`` used to reimplement
it inline with an *unguarded* rollback, awaited directly inside ``process_new_workout`` with no
surrounding try/except, so a raising rollback could break real workout ingestion (AUD-C14).

This test forbids a bare ``db.commit()`` / ``db.rollback()`` anywhere in a shadow-write
service: they must route through ``best_effort_write`` instead. ``capacity_floor_shadow_service``
passes naturally — it defers its commit to the observation it rides with and calls neither.
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SHADOW_SERVICES = sorted((_REPO_ROOT / "app" / "services").glob("*_shadow_service.py"))


def _direct_commit_rollback_calls(source: str) -> list[tuple[str, int]]:
    """Return (method, lineno) for every ``<something>.commit()`` / ``.rollback()`` call."""
    tree = ast.parse(source)
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"commit", "rollback"}
        ):
            hits.append((node.func.attr, node.lineno))
    return hits


def test_shadow_services_exist() -> None:
    """Guard the guard: a glob typo must not let the rule below pass vacuously."""
    assert len(_SHADOW_SERVICES) >= 5, (
        f"expected to find the shadow-write services under app/services/, found "
        f"{[p.name for p in _SHADOW_SERVICES]}"
    )


def test_shadow_services_do_not_inline_commit_or_rollback() -> None:
    offenders: dict[str, list[tuple[str, int]]] = {}
    for path in _SHADOW_SERVICES:
        hits = _direct_commit_rollback_calls(path.read_text(encoding="utf-8"))
        if hits:
            offenders[path.name] = hits

    assert not offenders, (
        "shadow-write services must delegate commit/rollback to "
        "telemetry_common.best_effort_write, not inline it:\n"
        + "\n".join(
            f"  {name}: " + ", ".join(f"{m}() at line {ln}" for m, ln in hits)
            for name, hits in offenders.items()
        )
    )
