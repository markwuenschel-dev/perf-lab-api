"""Normalize legacy parallel domain spellings to canonical DomainCode (ADR-0057).

The benchmark seed historically persisted three parallel spellings that are not
members of the canonical ``domain_vocab.DOMAINS`` set and were never aliased:
``mixed_modal`` / ``olympic_lifting`` / ``sprinting``. This is a pure metadata
correction — ``benchmark_definitions.code`` is the sole unique key and
observations reference by ``benchmark_definition_id``, so renaming the ``domain``
field collides with nothing.

Set-based and idempotent: safe on a clean (already-canonical) DB and safe to
re-run. Also normalizes the array-valued ``domain_lenses`` (surfacing-lens
DomainCodes, ADR-0057) and defensively any materialized ``objectives.domain`` /
``derived_metric_definitions.domain``. No authority or value is fabricated — only
the domain label is corrected.

The reverse mapping is lossy (``running`` was already a canonical value before
this migration), so ``downgrade`` is intentionally a no-op: we never re-introduce
the folded spellings.

Revision ID: a027_normalize_domain_codes
Revises: a026_dose_routing_shadow_log
Create Date: 2026-07-11
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a027_normalize_domain_codes"
down_revision: str | None = "a026_dose_routing_shadow_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# legacy spelling -> canonical DomainCode
_RENAMES: dict[str, str] = {
    "mixed_modal": "mixed",
    "olympic_lifting": "weightlifting",
    "sprinting": "running",
}


def upgrade() -> None:
    conn = op.get_bind()

    # Scalar `domain` columns on the tables that persist a home-domain DomainCode.
    # Bound params via sa.text() so this is driver-agnostic (asyncpg + psycopg2).
    for table in ("benchmark_definitions", "derived_metric_definitions", "objectives"):
        for legacy, canonical in _RENAMES.items():
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET domain = :canonical WHERE domain = :legacy"  # noqa: S608
                ),
                {"canonical": canonical, "legacy": legacy},
            )

    # Array-valued surfacing lens (`domain_lenses` on benchmark_definitions):
    # replace any legacy element in place, preserving order and the rest of the
    # array. array_replace is idempotent and a no-op when the element is absent.
    for legacy, canonical in _RENAMES.items():
        conn.execute(
            sa.text(
                "UPDATE benchmark_definitions "
                "SET domain_lenses = array_replace(domain_lenses, :legacy, :canonical) "
                "WHERE domain_lenses IS NOT NULL AND :legacy = ANY(domain_lenses)"
            ),
            {"canonical": canonical, "legacy": legacy},
        )


def downgrade() -> None:
    # Intentionally irreversible: the canonical targets (`mixed`/`weightlifting`/
    # `running`) are legitimate pre-existing values, so we cannot distinguish rows
    # that were renamed from rows that were always canonical. We never
    # re-introduce the folded spellings.
    pass
