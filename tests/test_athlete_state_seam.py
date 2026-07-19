"""Architecture guard: AthleteState is queried only behind the repository seam (AUD-C15).

The state-history route once inlined ``select(AthleteState)`` + ``unified_from_athlete_row``
— the exact query-plus-domain-conversion leak the repository boundary exists to prevent
(CONTEXT.md). After migrating those reads behind ``AthleteContextRepository`` +
``state_service`` loaders, this guard keeps the aggregate from leaking back: no production
route (``app/api``) or service (``app/services``) module may call ``select(AthleteState)``
directly.

Deliberately narrow: it gates *this one aggregate*, not all ORM queries, and only in
routes/services. The repository implementation is the sanctioned home; one-off maintenance
scripts (``app/scripts``) and migrations are out of scope, as is a column-only
``select(AthleteState.user_id, ...)`` projection (not the whole-entity load this guards).
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
# Production route + service modules — where a raw AthleteState load would be a seam leak.
SCANNED_ROOTS = [APP / "api", APP / "services"]
# The one sanctioned home for the query.
ALLOWED = {APP / "repositories" / "athlete_context_repository.py"}


def _athlete_state_select_lines(path: Path) -> list[int]:
    """Line numbers of ``select(AthleteState)`` calls (whole-entity load) in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "select"
            and any(isinstance(a, ast.Name) and a.id == "AthleteState" for a in node.args)
        ):
            hits.append(node.lineno)
    return hits


def test_scanned_roots_exist() -> None:
    """Guard the guard: a path typo would make the scan vacuously green."""
    for root in SCANNED_ROOTS:
        assert root.is_dir(), f"scanned root missing: {root}"


def test_no_direct_athlete_state_select_in_routes_or_services() -> None:
    offenders: dict[str, list[int]] = {}
    for root in SCANNED_ROOTS:
        for path in root.rglob("*.py"):
            if path in ALLOWED:
                continue
            lines = _athlete_state_select_lines(path)
            if lines:
                offenders[str(path.relative_to(APP))] = lines
    assert not offenders, (
        "AthleteState is loaded directly outside the repository seam: "
        f"{offenders}. Route/service code must go through AthleteContextRepository / the "
        "state_service loaders (CONTEXT.md), not inline select(AthleteState)."
    )
