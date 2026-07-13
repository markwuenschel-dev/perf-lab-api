"""Model ↔ migration structural-drift gate (INT-03).

After the Alembic chain is upgraded to head (the ``async_db`` fixture does this
on a freshly-dropped schema), autogenerate must detect no **structural** drift
between the live schema and ``Base.metadata`` — no table or column present in one
but missing from the other, and no column whose type silently changed. That is
the class of drift that breaks production queries.

Scope note (deliberate): autogenerate also reports *cosmetic* metadata drift —
column comments, ``NOT NULL`` flags, and index/constraint naming — that has
accumulated in this repo (109 such diffs at introduction, 0 structural). Those do
not change query correctness, and reconciling them is a separate, migration-heavy
cleanup (adding ``NOT NULL`` needs a data audit; renaming indexes is churn). This
gate intentionally excludes them so it stays a meaningful, green structural check
rather than a red backlog. The cosmetic categories are surfaced (printed) for
visibility. See the follow-up note in the INT quick-wins PR.

This is a ``requires_db`` test (uses ``async_db``); it skips only when Postgres is
unreachable, and fails loudly on real structural drift.
"""
import collections

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy.engine import Connection

# Import models so every table is registered on Base.metadata before comparison.
import app.models  # noqa: F401
from app.core.db import Base

pytestmark = pytest.mark.asyncio

# Autogenerate ops that change the schema *shape* — a query written against the
# models would break if the live schema disagreed on any of these.
STRUCTURAL_OPS = frozenset(
    {"add_table", "remove_table", "add_column", "remove_column", "modify_type"}
)


def _op_name(diff: object) -> str:
    """The operation string of a compare_metadata entry.

    Entries are either a tuple ``('add_table', ...)`` or, for grouped
    column-level modifications, a list whose first element is such a tuple.
    """
    if isinstance(diff, list):
        return str(diff[0][0])
    return str(diff[0])  # type: ignore[index]


def _diffs(sync_conn: Connection) -> list:
    context = MigrationContext.configure(
        connection=sync_conn,
        opts={
            # Compare column *types* too (Integer→BigInteger etc.). Server defaults
            # are excluded: Postgres round-trips them as dialect-specific text that
            # produces noisy false positives.
            "compare_type": True,
            "compare_server_default": False,
        },
    )
    return compare_metadata(context, Base.metadata)


async def test_no_structural_drift(async_db):
    """Models and the Alembic head agree on every table, column, and column type."""
    conn = await async_db.connection()
    diffs = await conn.run_sync(_diffs)

    by_op: collections.Counter[str] = collections.Counter(_op_name(d) for d in diffs)
    cosmetic = {op: n for op, n in by_op.items() if op not in STRUCTURAL_OPS}
    if cosmetic:
        # Visible, non-failing: the known metadata-drift backlog (INT-03 follow-up).
        print(f"[INT-03] non-structural (cosmetic) drift, not gated: {dict(cosmetic)}")

    structural = [d for d in diffs if _op_name(d) in STRUCTURAL_OPS]
    assert structural == [], (
        "Structural model/migration drift — a table, column, or column type "
        "differs between the models and the Alembic head. Generate a migration "
        "(or fix the model):\n" + "\n".join(f"  - {d}" for d in structural)
    )
