"""Macrocycle schema shape (Phase 5 — goal-anchored program plan).

Non-DB: asserts on ``Base.metadata`` (the mapped schema) directly, so this runs
regardless of Postgres availability. It guards the ADR-0040 decision — a
thin ``macrocycles`` container + a nullable ``mesocycle_blocks.macrocycle_id`` FK
with the right ondelete semantics — without needing a live database.
"""
from app.models import Base
from app.models.macrocycle import Macrocycle, MacrocycleStatus


def _table(name: str):
    assert name in Base.metadata.tables, f"{name} not registered on Base.metadata"
    return Base.metadata.tables[name]


def test_macrocycles_table_columns():
    t = _table("macrocycles")
    assert set(t.columns.keys()) == {
        "id",
        "user_id",
        "objective_id",
        "start_date",
        "status",
        "created_at",
        "updated_at",
    }
    # target_date is deliberately NOT stored — it is read from the anchor
    # Objective at compute time (ADR-0040 / PDR-0004 single source of truth).
    assert "target_date" not in t.columns


def test_macrocycle_anchor_fk_cascades_from_objective():
    t = _table("macrocycles")
    objective_id = t.columns["objective_id"]
    assert objective_id.nullable is False  # the anchor is required
    fk = next(iter(objective_id.foreign_keys))
    assert fk.column.table.name == "objectives"
    assert fk.ondelete == "CASCADE"  # delete the objective → drop its macrocycle


def test_macrocycle_user_fk_present():
    user_id = _table("macrocycles").columns["user_id"]
    assert user_id.nullable is False
    fk = next(iter(user_id.foreign_keys))
    assert fk.column.table.name == "users"


def test_block_macrocycle_fk_is_nullable_and_set_null():
    """The block→macrocycle link is nullable (existing blocks unaffected) and
    detaches rather than deletes on macrocycle removal."""
    col = _table("mesocycle_blocks").columns["macrocycle_id"]
    assert col.nullable is True
    fk = next(iter(col.foreign_keys))
    assert fk.column.table.name == "macrocycles"
    assert fk.ondelete == "SET NULL"


def test_macrocycle_status_enum_values():
    assert [s.value for s in MacrocycleStatus] == ["active", "achieved", "abandoned"]


def test_macrocycle_relationships_are_mapped():
    rels = Macrocycle.__mapper__.relationships
    assert {"user", "objective", "blocks"} <= set(rels.keys())
    # blocks is the one-to-many back-ref that MesocycleBlock.macrocycle points at.
    assert rels["blocks"].mapper.class_.__name__ == "MesocycleBlock"
    assert rels["objective"].mapper.class_.__name__ == "Objective"
