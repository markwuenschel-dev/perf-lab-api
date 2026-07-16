"""Fitness check: every engine feature flag is read by production code.

A flag defined in ``app/engine/feature_flags.py`` but referenced by no production module is a
fictional promotion gate — it implies a live OFF/ON path exists when it does not. Five such
flags were removed in AUD-C9; this guard stops the class from coming back: add a flag before
its consumer and CI fails until the consumer exists.

Scope + limits: this checks that each flag has a *reader* (a real code reference, not a comment
or docstring — the walk is AST-based). It cannot prove the reader makes the flag *matter*; a
genuine promotion flag additionally needs tests for both its OFF and ON behavior and an
owner/removal condition (see feature_flags.py). Reference existence is the mechanically
enforceable floor.
"""
import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
FLAGS_FILE = APP_DIR / "engine" / "feature_flags.py"


def _declared_flags() -> list[str]:
    """Module-level constant names assigned in feature_flags.py."""
    tree = ast.parse(FLAGS_FILE.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, ast.Assign):
            names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _names_referenced_in_app() -> set[str]:
    """Every flag name reachable as a real code reference anywhere in app/, excluding
    feature_flags.py itself. Counted: a ``Name`` / attribute access / imported symbol, and a
    string literal passed as the attribute-name argument to ``getattr(...)`` (the dynamic-read
    pattern, e.g. ``getattr(feature_flags, "FLAG", default)``). Deliberately NOT counted: bare
    string literals in comments/docstrings, so a flag merely *mentioned* in prose still reads as
    unwired."""
    referenced: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        if path == FLAGS_FILE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
            elif isinstance(node, ast.ImportFrom):
                referenced.update(alias.name for alias in node.names)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                referenced.add(node.args[1].value)
    return referenced


def test_flags_are_declared() -> None:
    """Guard the guard: an empty flag list would make the reader check vacuously green."""
    assert _declared_flags(), "no flags declared in feature_flags.py — the reader check is vacuous"


def test_every_feature_flag_has_a_production_reader() -> None:
    referenced = _names_referenced_in_app()
    unread = [flag for flag in _declared_flags() if flag not in referenced]
    assert not unread, (
        "feature flag(s) read by no production code — a flag that gates nothing is a fictional "
        f"promotion gate: {unread}. Wire the consumer, or remove the flag (its maturity belongs "
        "in an ADR/roadmap, not runtime config)."
    )
