"""Fitness check: every test function makes at least one assertion.

A ``def test_*`` with no assertion passes on the mere absence of an exception. Sometimes
that is intentional (a "does not raise" guard) — but written as a bare call it is
indistinguishable from a test someone forgot to finish, and it silently protects nothing
if the code under it turns into a no-op. This file walks the suite and requires every test
to carry an assertion signal, so the intentional no-raise cases must say so explicitly (via
``assert_does_not_raise()`` in conftest) rather than looking accidentally empty.

An "assertion signal" is any of: an ``assert`` statement; a call to ``pytest.raises`` /
``pytest.warns`` / ``pytest.fail`` / ``pytest.xfail``; or a call to a helper whose name
starts with ``assert`` (custom ``assert_*`` helpers and ``assert_does_not_raise``).
"""
import ast
from pathlib import Path

TESTS_DIR = Path(__file__).parent
_PYTEST_ASSERTION_CALLS = {"raises", "warns", "fail", "xfail"}


def _has_assertion_signal(func: ast.AST) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id.startswith("assert"):
                return True
            if isinstance(fn, ast.Attribute):
                if fn.attr.startswith("assert") or fn.attr in _PYTEST_ASSERTION_CALLS:
                    return True
    return False


def _test_functions() -> list[tuple[str, str, ast.AST]]:
    found: list[tuple[str, str, ast.AST]] = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test_"
            ):
                found.append((path.name, node.name, node))
    return found


def test_the_walk_finds_tests() -> None:
    """Guard the guard: an empty walk would make the assertion check vacuously green."""
    funcs = _test_functions()
    assert len(funcs) > 500, f"expected the full suite, walked only {len(funcs)} test functions"


def test_every_test_makes_an_assertion() -> None:
    assertionless = [
        f"{filename}::{name}"
        for filename, name, func in _test_functions()
        if not _has_assertion_signal(func)
    ]
    assert not assertionless, (
        "test function(s) with no assertion — they pass on the absence of an exception and "
        "protect nothing. Add an assertion, or wrap a deliberate no-raise act in "
        "conftest.assert_does_not_raise():\n" + "\n".join(assertionless)
    )
