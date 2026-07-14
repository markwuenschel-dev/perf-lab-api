"""Guard: ``StrengthDeclineShadow`` has no runtime writer (INT-02, W1-C1 expand step).

W1-C1 ships the shadow table as deployment-enabling infrastructure only — the database
can accept rows, and nothing writes them. This guard fails the moment a runtime writer
appears without going through W1-C2's contract.

Why the writer was deferred rather than shipped with the schema: the first attempt
persisted the row from inside ``resolve_prescription_basis``, which runs *before*
prescription's ``db.commit()`` (``prescription_service.py:380``). A shadow-row failure at
flush would have taken the whole prescription down with it — and the ``try: db.add(row)``
guard around it caught nothing, because ``db.add()`` stages in memory and does no I/O; the
real failure happens later, at commit, in the caller's transaction. The repo's own
convention is documented at ``prescription_service.py:382-383``: telemetry is written
*after* the commit "so a telemetry failure can never alter or block ``rx``".

**This file must be DELETED as part of W1-C2, not weakened.** Its whole purpose is to make
the inert state a deliberate, enforced choice rather than a thing that quietly rots. W1-C2
replaces it with tests that inject failures at the real I/O points (flush/commit), prove
prescription survives a shadow-write failure, and prove concurrent duplicates resolve to
exactly one row via ``ON CONFLICT DO NOTHING``.
"""
import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "app"
MODEL_MODULE = "app/models/strength_decline_shadow.py"

# The model's own module is where the class is legitimately defined; every other
# reference under app/ is a writer (or a read path, equally forbidden — the shadow
# table must never feed back into prescription).
_REFERENCE = re.compile(r"\bStrengthDeclineShadow\b|\bstrength_decline_shadow\b")


def test_no_runtime_reference_to_strength_decline_shadow():
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        rel = path.relative_to(APP_ROOT.parent).as_posix()
        if rel == MODEL_MODULE:
            continue
        # Model registration is required for metadata/migration parity, not a writer.
        if rel == "app/models/__init__.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _REFERENCE.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "StrengthDeclineShadow is referenced from runtime app code, but W1-C1 ships the "
        "table inert — the writer belongs to W1-C2, which must persist AFTER the "
        "production prescription commit in an independent best-effort transaction using "
        "INSERT ... ON CONFLICT DO NOTHING. If this is W1-C2, delete this guard file "
        "rather than editing it:\n" + "\n".join(offenders)
    )


def test_model_is_importable_and_registered():
    """The table must exist in metadata even though nothing writes it — that is what
    makes W1-C1 a deployable expand step rather than dead code."""
    from app.core.db import Base
    from app.models.strength_decline_shadow import StrengthDeclineShadow

    assert StrengthDeclineShadow.__tablename__ == "strength_decline_shadow"
    assert "strength_decline_shadow" in Base.metadata.tables


def test_unique_constraint_is_the_concurrency_authority():
    """W1-C2 relies on this constraint to arbitrate concurrent inserts atomically
    instead of a SELECT-before-INSERT check (a TOCTOU race). If the constraint is ever
    dropped or its columns change, W1-C2's idempotency silently stops being atomic."""
    from app.models.strength_decline_shadow import StrengthDeclineShadow

    table = StrengthDeclineShadow.__table__
    uniques = {
        tuple(sorted(c.name for c in con.columns)): con.name
        for con in table.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    }
    expected = ("capacity_axis", "decline_policy_version", "trigger_observation_id")
    assert expected in uniques, f"expected unique on {expected}, found {uniques}"
    assert uniques[expected] == "uq_strength_decline_shadow_trigger_axis_policy"


def test_unique_constraint_columns_are_all_not_null():
    """SQL treats NULLs as DISTINCT, so a UNIQUE constraint containing a nullable
    column silently stops enforcing — duplicates insert cleanly and raise nothing.

    This is not hypothetical: the first cut of this table shipped
    ``trigger_observation_id`` as nullable=True, and three byte-identical rows
    inserted with no error. That would have made W1-C2's INSERT ... ON CONFLICT
    DO NOTHING a silent no-op and let shadow rows accumulate unbounded.

    Every column in the constraint must therefore be NOT NULL. Mirrors
    strength_decline_candidate (a032), whose trigger_observation_id is nullable=False.
    """
    from app.models.strength_decline_shadow import StrengthDeclineShadow

    table = StrengthDeclineShadow.__table__
    constraint = next(
        con
        for con in table.constraints
        if getattr(con, "name", None) == "uq_strength_decline_shadow_trigger_axis_policy"
    )
    nullable = sorted(c.name for c in constraint.columns if c.nullable)
    assert not nullable, (
        "these columns participate in the unique constraint but are nullable, which "
        f"silently defeats it (NULL != NULL in SQL): {nullable}"
    )


def test_shadow_rows_are_append_only_telemetry():
    """decision_impact is the family-wide marker that a table never influences
    production decisions (mirrors capacity_floor_shadow_log, ekf_shadow_log, etc.)."""
    from app.models.strength_decline_shadow import StrengthDeclineShadow

    column = StrengthDeclineShadow.__table__.c.decision_impact
    assert column.default is not None, "decision_impact must default to none_shadow_only"
    assert column.default.arg == "none_shadow_only"
