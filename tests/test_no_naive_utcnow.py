"""Guard: no runtime ``datetime.utcnow()`` calls in app code (INT-19).

``datetime.utcnow()`` returns a *naive* datetime and is deprecated in Python 3.12+.
INT-19 replaced every call site with ``datetime.now(UTC)`` (tz-aware) or
``datetime.now(UTC).replace(tzinfo=None)`` where a naive value is required to match
an existing naive ``DateTime`` column (asyncpg rejects mixing aware values with
naive ``timestamp`` columns). This test fails the moment a naive ``.utcnow()`` call
creeps back in.

Scope note: the *callable* form ``default=datetime.utcnow`` still used as a
SQLAlchemy column default is intentionally NOT flagged here — retiring those is a
schema-evolution concern (naive → ``timestamptz`` migration) tracked separately
(INT-15), not part of INT-19. The regex below only matches the call form
``.utcnow(`` and therefore leaves ``default=datetime.utcnow`` alone.
"""
import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# Matches the *call* form `.utcnow(` but not the bare callable `default=datetime.utcnow`.
_UTCNOW_CALL = re.compile(r"\.utcnow\s*\(")


def test_no_naive_utcnow_calls_in_app():
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _UTCNOW_CALL.search(line):
                rel = path.relative_to(APP_ROOT.parent).as_posix()
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Naive datetime.utcnow() calls found — use datetime.now(UTC) (or "
        ".replace(tzinfo=None) when a naive column requires it), per INT-19:\n"
        + "\n".join(offenders)
    )
