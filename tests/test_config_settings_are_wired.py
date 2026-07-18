"""Fitness check: every bool Settings field is read by production code.

A ``bool`` field on ``app/core/config.py`` ``Settings`` is a behavior toggle. One defined but
referenced by no production module is an unwired promotion gate — the config-level twin of the
AUD-C9 engine flags. ``USE_STRUCTURED_COACHING_TEMPLATES`` was exactly this (a dead
``# Future features`` toggle for an already-live, unconditional feature), removed in AUD-C10.
The feature-flag guard (``test_feature_flags_are_wired.py``) only watched
``app/engine/feature_flags.py``; this extends the same reader check to ``Settings`` so the
class can't recur: add a bool toggle before its consumer and CI fails until the consumer exists.

Scope + limits: the ``bool`` type is the toggle signal — infra fields (DB URL, secrets, CORS,
tokens) are typed ``str``/``int`` and are out of scope. Like the feature-flag guard, this proves
a *reader* exists (a real AST reference, not a comment) — the mechanically enforceable floor —
not that the reader makes the toggle matter.
"""
import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
CONFIG_FILE = APP_DIR / "core" / "config.py"


def _bool_settings_fields() -> list[str]:
    """Names of ``bool``-annotated fields on the ``Settings`` class in config.py."""
    tree = ast.parse(CONFIG_FILE.read_text(encoding="utf-8"))
    fields: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and isinstance(stmt.annotation, ast.Name)
                    and stmt.annotation.id == "bool"
                ):
                    fields.append(stmt.target.id)
    return fields


def _names_referenced_in_app() -> set[str]:
    """Every name reachable as a real code reference anywhere in app/, excluding config.py.

    Same detection as the feature-flag guard: a ``Name`` / attribute access / imported symbol,
    and a string literal passed as the attribute-name argument to ``getattr(...)``. Deliberately
    NOT counted: bare string literals in comments/docstrings, so a field merely *mentioned* in
    prose still reads as unwired.
    """
    referenced: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        if path == CONFIG_FILE:
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


def test_bool_settings_fields_exist() -> None:
    """Guard the guard: zero bool fields would make the reader check vacuously green."""
    assert _bool_settings_fields(), (
        "no bool fields found on Settings — the reader check is vacuous (or config.py moved)"
    )


def test_every_bool_setting_has_a_production_reader() -> None:
    referenced = _names_referenced_in_app()
    unread = [name for name in _bool_settings_fields() if name not in referenced]
    assert not unread, (
        "bool Settings field(s) read by no production code — a config toggle that gates "
        f"nothing is an unwired promotion gate: {unread}. Wire the consumer, or remove the "
        "field (feature maturity belongs in an ADR/roadmap, not runtime config)."
    )
