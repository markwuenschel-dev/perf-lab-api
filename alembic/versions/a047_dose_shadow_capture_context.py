"""Dose shadow log: who the athlete was and what the planner asked for (phase 8.2).

Four nullable columns on ``dose_model_shadow_log`` so a fit can be checked for coverage
across athlete level, workload preference and the prescription the session fulfilled:

* ``experience_level`` — ``athlete_profiles.experience_level`` at ingest (null: no profile).
* ``workload_preference`` — the EFFECTIVE easy/medium/hard of the linked session's block
  (``app.logic.planning.normalize_intensity``: an unset preference IS medium).
* ``workload_preference_defaulted`` — true when that value came from the default, not a choice.
* ``prescription_branch`` — the linked prescription's ``why.prescription_branch``: the
  prescriber branch id (a template's ``branch_id`` on the goal path; a safety/readiness path
  id otherwise). Named for what it is, not as a template id.

Rows written before this migration stay null. Shadow table only; nothing reads these into
state or a recommendation.

Revision ID: a047_dose_shadow_context
Revises: a046_dose_model_shadow_log
Create Date: 2026-09-27
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a047_dose_shadow_context"
down_revision: str | None = "a046_dose_model_shadow_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "dose_model_shadow_log"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("experience_level", sa.String(length=40), nullable=True))
    op.add_column(_TABLE, sa.Column("workload_preference", sa.String(length=10), nullable=True))
    op.add_column(
        _TABLE, sa.Column("workload_preference_defaulted", sa.Boolean(), nullable=True)
    )
    op.add_column(_TABLE, sa.Column("prescription_branch", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "prescription_branch")
    op.drop_column(_TABLE, "workload_preference_defaulted")
    op.drop_column(_TABLE, "workload_preference")
    op.drop_column(_TABLE, "experience_level")
