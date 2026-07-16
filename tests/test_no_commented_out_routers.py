"""Fitness check: app/main.py carries no commented-out router includes.

A ``# app.include_router(blocks.router, ...)`` left in the router section is dead
scaffolding: it reads as a planned or temporarily-disabled route, but nothing wires it,
and the module it names may not even exist (``blocks``, ``onboarding`` — both referenced
by comments that outlived their deletion, and both served, if at all, by differently
named routers). A reader auditing what the app exposes cannot trust the list. Live
routers are included; disabled ones are deleted, not commented out. This keeps the
router manifest honest.
"""
import re
from pathlib import Path

import app.main

_COMMENTED_INCLUDE = re.compile(r"^\s*#\s*app\.include_router\b")


def test_main_has_no_commented_out_router_includes() -> None:
    source = Path(app.main.__file__).read_text(encoding="utf-8").splitlines()
    offenders = [
        f"{app.main.__file__}:{i}: {line.strip()}"
        for i, line in enumerate(source, start=1)
        if _COMMENTED_INCLUDE.match(line)
    ]
    assert not offenders, (
        "commented-out router include(s) in app/main.py — dead scaffolding that "
        "misrepresents what the app serves. Delete the line, or wire the router for real:\n"
        + "\n".join(offenders)
    )
