"""AUD-C8 replay: every EKF appender serializes the per-user belief chain (a038).

Head-correction replay is only safe if a replay cannot append from a stale head while a
concurrent unrelated transition (workout predict / benchmark update / another wellness event)
advances the same per-user chain. The guarantee is a shared, namespaced, transaction-scoped
per-user advisory lock acquired by *every* appender before any chain-dependent read or append —
a replay-only lock would not stop the ordinary appenders from forking the chain.

This is a structural guard (AST): each appender must call ``_acquire_ekf_chain_lock``. It also
pins that ``users.id`` is a 32-bit Integer, so the two-int ``pg_advisory_xact_lock(ns, user_id)``
form is exact — a BIGINT id would need a 64-bit key scheme to avoid silent truncation.
"""
import ast
from pathlib import Path

from sqlalchemy import BigInteger, Integer

from app.models.user import User

_SERVICE = Path(__file__).resolve().parents[1] / "app" / "services" / "ekf_shadow_service.py"
_LOCK_HELPER = "_acquire_ekf_chain_lock"

# Every function that appends a row to ekf_shadow_log (the per-user belief chain).
_APPENDERS = frozenset(
    {"record_ekf_predict", "record_ekf_update", "record_ekf_wellness_observation"}
)


def _calls_in(func: ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


def _appender_defs() -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(_SERVICE.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name in _APPENDERS
    }


def test_every_appender_is_present() -> None:
    """Guard the guard: a renamed appender must not let the check pass vacuously."""
    assert set(_appender_defs()) == _APPENDERS


def test_every_appender_acquires_the_shared_chain_lock() -> None:
    defs = _appender_defs()
    missing = sorted(name for name, fn in defs.items() if _LOCK_HELPER not in _calls_in(fn))
    assert not missing, (
        f"EKF appenders must serialize the per-user chain via {_LOCK_HELPER}() before any "
        f"chain read/append; these do not: {missing}. A replay-only lock cannot stop these "
        "ordinary appenders from advancing the head under a concurrent replay."
    )


def test_wellness_replay_also_acquires_the_lock() -> None:
    """The replay path itself must serialize on the same lock (belt with the appenders)."""
    tree = ast.parse(_SERVICE.read_text(encoding="utf-8"))
    orchestrator = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "record_ekf_wellness_observation"
    )
    # The orchestrator acquires the lock in BOTH the main and the post-classification replay txn.
    lock_calls = [
        node for node in ast.walk(orchestrator)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == _LOCK_HELPER
    ]
    assert len(lock_calls) >= 2, (
        "the wellness orchestrator must acquire the chain lock in both the main shadow "
        "transaction and the separate post-classification replay transaction"
    )


def test_user_id_is_int4_so_the_two_int_advisory_key_is_exact() -> None:
    id_type = User.__table__.c.id.type
    assert isinstance(id_type, Integer) and not isinstance(id_type, BigInteger), (
        "users.id must be a 32-bit Integer for pg_advisory_xact_lock(ns, user_id); a BigInteger "
        "id would silently truncate in the two-int form and need a 64-bit advisory-key scheme"
    )
