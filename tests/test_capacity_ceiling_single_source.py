"""Guard: the capacity-axis ceiling has exactly one home (INT-12).

The ``650.0 / 100.0`` capacity ceiling was copy-pasted as a private
``_capacity_ceiling`` across the EKF packing, MPC objective, and state-update
modules. It now lives once in ``app.domain.vectors.capacity_ceiling``. This test
fails if a fresh inline copy of the ceiling literal reappears anywhere under
``app/`` — keeping the single source of truth enforced, not just conventional.
"""
import re
from pathlib import Path

from app.domain.vectors import (
    AEROBIC_CEILING,
    DEFAULT_CAPACITY_CEILING,
    capacity_ceiling,
)

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# The exact shape of the old duplicated one-liner:  `650.0 if <...> "aerobic" ...`
_CEILING_LITERAL = re.compile(r"650\.0\s+if\b.*aerobic")


def test_capacity_ceiling_values():
    assert capacity_ceiling("aerobic") == AEROBIC_CEILING == 650.0
    for axis in ("max_strength", "power", "hypertrophy", "work_capacity", "glycolytic"):
        assert capacity_ceiling(axis) == DEFAULT_CAPACITY_CEILING == 100.0


def test_no_inline_ceiling_copies_under_app():
    """No module may re-declare the aerobic-ceiling branch inline."""
    offenders = [
        py.relative_to(APP_ROOT).as_posix()
        for py in APP_ROOT.rglob("*.py")
        if "mypy_cache" not in py.parts
        for line in [py.read_text(encoding="utf-8")]
        if _CEILING_LITERAL.search(line)
    ]
    assert not offenders, (
        "Inline capacity-ceiling literal found — import "
        f"`app.domain.vectors.capacity_ceiling` instead. Offenders: {offenders}"
    )
