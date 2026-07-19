"""Shrink-only allowlist over naive ``datetime.utcnow`` column defaults (AUD-C18).

Full ruff ``DTZ`` enforcement is owned by INT-15 (the naive→timestamptz migration). Until
then, ``datetime.utcnow`` is deprecated and returns a *naive* datetime, and enabling DTZ
globally now would either mass-change behavior ahead of that migration or normalize dozens
of suppressions. Instead, freeze the existing naive-utcnow column-default sites as a
**shrink-only allowlist**: a NEW site fails CI, and when INT-15 migrates a column its
allowlist entry must be removed too — so a delisted site cannot silently return.

Sites are keyed by ``(file, column, keyword)`` — stable across line moves, unlike raw line
numbers. No blanket ``# noqa`` / file-level ignore is used. This guard is transitional and
should be deleted when ruff DTZ becomes authoritative under INT-15.
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# The naive-utcnow column-default sites present when this guard was installed (AUD-C18).
# SHRINK-ONLY: delete an entry when INT-15 migrates its column; never add one for new code.
NAIVE_UTCNOW_ALLOWLIST: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("app/models/athlete_state.py", "timestamp", "default"),
        ("app/models/benchmark_definition.py", "created_at", "default"),
        ("app/models/benchmark_observation.py", "observed_at", "default"),
        ("app/models/capacity_floor_shadow.py", "created_at", "default"),
        ("app/models/derived_metric_snapshot.py", "computed_at", "default"),
        ("app/models/dose_routing_shadow.py", "created_at", "default"),
        ("app/models/ekf_shadow.py", "created_at", "default"),
        ("app/models/experiment.py", "assigned_at", "default"),
        ("app/models/macrocycle.py", "created_at", "default"),
        ("app/models/macrocycle.py", "updated_at", "default"),
        ("app/models/macrocycle.py", "updated_at", "onupdate"),
        ("app/models/mesocycle.py", "created_at", "default"),
        ("app/models/mesocycle.py", "updated_at", "default"),
        ("app/models/mesocycle.py", "updated_at", "onupdate"),
        ("app/models/mpc_shadow.py", "created_at", "default"),
        ("app/models/objective.py", "created_at", "default"),
        ("app/models/personalization_shadow.py", "created_at", "default"),
        ("app/models/planning_override.py", "created_at", "default"),
        ("app/models/planning_override.py", "updated_at", "default"),
        ("app/models/planning_override.py", "updated_at", "onupdate"),
        ("app/models/recovery_shadow.py", "created_at", "default"),
        ("app/models/strength_decline_candidate.py", "created_at", "default"),
        ("app/models/strength_decline_shadow.py", "computed_at", "default"),
        ("app/models/telemetry.py", "created_at", "default"),
        ("app/models/user.py", "created_at", "default"),
        ("app/models/user.py", "updated_at", "default"),
        ("app/models/user.py", "updated_at", "onupdate"),
        ("app/models/weak_point.py", "detected_at", "default"),
        ("app/models/wearable_connection.py", "created_at", "default"),
        ("app/models/wearable_connection.py", "updated_at", "default"),
        ("app/models/wearable_connection.py", "updated_at", "onupdate"),
        ("app/models/wellness.py", "created_at", "default"),
        ("app/models/workout_log.py", "logged_at", "default"),
        ("app/models/workout_set_log.py", "created_at", "default"),
    }
)


def _is_utcnow(node: ast.expr) -> bool:
    """True for ``datetime.utcnow`` / ``datetime.utcnow()`` / bare ``utcnow``."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr == "utcnow"
    if isinstance(node, ast.Name):
        return node.id == "utcnow"
    return False


def _naive_utcnow_sites() -> set[tuple[str, str, str]]:
    """Every ``mapped_column(..., default/onupdate=datetime.utcnow)`` site under app/,
    keyed by (repo-relative file, column name, keyword)."""
    sites: set[tuple[str, str, str]] = set()
    for path in APP.rglob("*.py"):
        rel = str(path.relative_to(APP.parent)).replace("\\", "/")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            target: str | None = None
            value: ast.expr | None = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target, value = node.target.id, node.value
            elif (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                target, value = node.targets[0].id, node.value
            if target is None or not isinstance(value, ast.Call):
                continue
            for kw in value.keywords:
                if kw.arg in ("default", "onupdate") and _is_utcnow(kw.value):
                    sites.add((rel, target, kw.arg))
    return sites


def test_no_new_naive_utcnow_site() -> None:
    new = _naive_utcnow_sites() - NAIVE_UTCNOW_ALLOWLIST
    assert not new, (
        "new naive datetime.utcnow column default(s) — utcnow is deprecated and returns a "
        f"naive datetime: {sorted(new)}. Use a timezone-aware default "
        "(e.g. `default=lambda: datetime.now(UTC)`); if this is INT-15 work, migrate the "
        "column rather than adding to the allowlist."
    )


def test_removed_naive_utcnow_site_is_delisted() -> None:
    stale = NAIVE_UTCNOW_ALLOWLIST - _naive_utcnow_sites()
    assert not stale, (
        "naive-utcnow site(s) in the allowlist no longer exist (good — INT-15 progress): "
        f"{sorted(stale)}. Remove them from NAIVE_UTCNOW_ALLOWLIST so the reduction is "
        "locked in (shrink-only) and the site cannot silently return."
    )
