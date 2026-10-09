"""Why a late event stayed out of the state (P3b-3).

``workout_logs.replay_refusal`` and ``benchmark_observations.replay_refusal`` hold the stable code
(``tail_replay_service``) that kept a record-only event out of the state when ``fold_late_events``
tried to fold it in: ``window_exceeded``, ``untrusted_checkpoint``, ``decline_policy_engaged``,
``internal_error`` and so on. NULL means a fold was never attempted, or it succeeded.

Mutable on purpose: a later attempt overwrites it. Nothing else changes.

Revision ID: a057_replay_refusal
Revises: a056_state_corrections
Create Date: 2026-10-09
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a057_replay_refusal"
down_revision: str | None = "a056_state_corrections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("workout_logs", "benchmark_observations")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("replay_refusal", sa.String(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "replay_refusal")
