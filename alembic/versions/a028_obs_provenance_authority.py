"""Five-dimension observation provenance + policy-derived capacity authority (ADR-0058).

Additive, nullable columns that let capacity authority be *derived* from provenance
(min of independent caps) instead of asserted on a free-form source string. The
permanent structural fix for the PR1 corruption class.

Conservative backfill only — NO migration elevates authority from an old label.
Every pre-existing row is stamped as a schema backfill: workout extraction →
system/workout/none; everything else ambiguous → legacy_unknown/none. New writes
get real provenance from the service resolver. NOT VALID guard constraints encode
the ADR invariants without blocking deploy on historical dirt.

Revision ID: a028_obs_provenance_authority
Revises: a027_normalize_domain_codes
Create Date: 2026-07-11
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a028_obs_provenance_authority"
down_revision: str | None = "a027_normalize_domain_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_T = "benchmark_observations"

_NEW_COLUMNS = [
    ("source_type", sa.String(40)),
    ("collection_mode", sa.String(40)),
    ("provenance_operation", sa.String(30)),
    ("migration_version", sa.String(40)),
    ("migrated_at", sa.DateTime()),
    ("actor_type", sa.String(20)),
    ("requested_capacity_effect", sa.String(30)),
    ("capacity_effect", sa.String(30)),
    ("protocol_code", sa.String(100)),
    ("protocol_version", sa.String(30)),
    ("protocol_validity", sa.String(20)),
    ("authority_policy_version", sa.String(40)),
    ("authority_resolution_reason", sa.Text()),
    # Confidence hook (ADR-0058 structural; #106 assigns the numbers). `confidence`
    # itself already exists (a025) — these describe where it came from.
    ("confidence_source", sa.String(40)),
    ("confidence_model_version", sa.String(30)),
]

_VALID_EFFECTS = "('none','initialize_prior','upward_lower_bound','bidirectional_update')"


def upgrade() -> None:
    for name, type_ in _NEW_COLUMNS:
        op.add_column(_T, sa.Column(name, type_, nullable=True))

    # --- Conservative backfill -------------------------------------------------
    # Workout-derived rows: system actor, workout mode, no capacity authority.
    op.execute(
        f"""
        UPDATE {_T} SET
            source_type = 'workout_extraction',
            collection_mode = 'workout',
            actor_type = 'system',
            capacity_effect = 'none',
            protocol_validity = 'not_evaluated',
            provenance_operation = 'schema_backfill',
            migration_version = 'a028',
            migrated_at = now(),
            authority_policy_version = 'authority_policy_v1',
            authority_resolution_reason = 'schema_backfill_conservative_workout'
        WHERE source = 'workout_extraction'
        """
    )
    # Everything else is ambiguous legacy history — never fabricate it into a
    # measurement. legacy_unknown / none. (Historical rows were already applied to
    # state at write time; this label change does not re-process them.)
    op.execute(
        f"""
        UPDATE {_T} SET
            source_type = 'legacy_unknown',
            collection_mode = 'legacy_unknown',
            actor_type = 'unknown',
            capacity_effect = 'none',
            protocol_validity = 'not_evaluated',
            provenance_operation = 'schema_backfill',
            migration_version = 'a028',
            migrated_at = now(),
            authority_policy_version = 'authority_policy_v1',
            authority_resolution_reason = 'schema_backfill_conservative_legacy'
        WHERE source <> 'workout_extraction'
        """
    )

    # --- Guard constraints (NOT VALID; historical dirt must not block deploy) ---
    for name, expr in (
        (
            "chk_capacity_effect_enum",
            f"capacity_effect IS NULL OR capacity_effect IN {_VALID_EFFECTS}",
        ),
        (
            "chk_requested_effect_enum",
            f"requested_capacity_effect IS NULL OR requested_capacity_effect IN {_VALID_EFFECTS}",
        ),
        (
            "chk_workout_no_bidirectional",
            "source_type IS DISTINCT FROM 'workout_extraction' "
            "OR capacity_effect IS DISTINCT FROM 'bidirectional_update'",
        ),
        (
            "chk_onramp_no_bidirectional",
            "collection_mode IS DISTINCT FROM 'onboarding_onramp' "
            "OR capacity_effect IS DISTINCT FROM 'bidirectional_update'",
        ),
        (
            "chk_bidirectional_needs_valid_protocol",
            "capacity_effect IS DISTINCT FROM 'bidirectional_update' "
            "OR protocol_validity = 'valid'",
        ),
    ):
        op.execute(f"ALTER TABLE {_T} ADD CONSTRAINT {name} CHECK ({expr}) NOT VALID")


def downgrade() -> None:
    for name in (
        "chk_capacity_effect_enum",
        "chk_requested_effect_enum",
        "chk_workout_no_bidirectional",
        "chk_onramp_no_bidirectional",
        "chk_bidirectional_needs_valid_protocol",
    ):
        op.execute(f"ALTER TABLE {_T} DROP CONSTRAINT IF EXISTS {name}")
    for name, _ in reversed(_NEW_COLUMNS):
        op.drop_column(_T, name)
