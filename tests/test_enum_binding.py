"""
Regression guard for the native-enum binding on mesocycle models.

The Postgres enum types (blockgoal/blockstatus/sessionstatus) were created from
the enum *values* (e.g. ``"active"``), but SQLAlchemy's default binds the member
*name* (``"ACTIVE"``). That mismatch made every block/planned-session read raise
``invalid input value for enum ...`` — which surfaced once the Twin/Planning UI
started calling the prescriber and planning endpoints. ``values_callable`` on the
columns fixes it. These checks inspect the compiled column type (no DB needed),
so a future edit dropping ``values_callable`` fails here instead of at runtime.
"""

from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)


def test_block_columns_bind_enum_values_not_names() -> None:
    assert set(MesocycleBlock.__table__.c.status.type.enums) == {b.value for b in BlockStatus}
    assert set(MesocycleBlock.__table__.c.goal.type.enums) == {g.value for g in BlockGoal}
    # Guard the specific casing that broke: values are lower/title-case, never the
    # upper-case member names.
    assert "ACTIVE" not in MesocycleBlock.__table__.c.status.type.enums
    assert "active" in MesocycleBlock.__table__.c.status.type.enums


def test_planned_session_status_binds_enum_values() -> None:
    assert set(PlannedSession.__table__.c.status.type.enums) == {s.value for s in SessionStatus}
    assert "PENDING" not in PlannedSession.__table__.c.status.type.enums
    assert "pending" in PlannedSession.__table__.c.status.type.enums
