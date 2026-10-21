"""INT-05 C4 guard: decision logic reads the canonical vectors, never the legacy scalar mirror.

Scans every module under ``app/logic`` (prescriber, finalize, constraint engine, ...) for a
READ of a mirror field: ``x.f_met_systemic`` (attribute load), ``d["f_met_systemic"]``
(subscript load) or ``d.get("f_met_systemic")``. Writes of the mirror are not reads, and the
StateEvidence axis strings (``_DriverRule("f_nm_central", ...)``) are ids, not reads, so
neither is flagged. A file that legitimately touches the mirror is listed with its reason.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

LOGIC = Path(__file__).resolve().parents[1] / "app" / "logic"

MIRROR_FIELDS = frozenset({
    "c_met_aerobic", "c_nm_force", "c_struct", "b_met_anaerobic",
    "f_met_systemic", "f_nm_peripheral", "f_nm_central", "f_struct_damage",
})

ALLOWED: dict[str, str] = {
    # Writes the mirror: reads the dict sync_legacy_from_vectors returns, then assigns it.
    "state_update_v0.py": "mirror writer",
    # Integrity check: compares the stored columns against a replayed row.
    "tail_replay.py": "replay integrity comparison",
}


def mirror_reads(source: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Attribute) and node.attr in MIRROR_FIELDS
                and isinstance(node.ctx, ast.Load)):
            found.append((node.lineno, f".{node.attr}"))
        elif (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
              and isinstance(node.slice, ast.Constant) and node.slice.value in MIRROR_FIELDS):
            found.append((node.lineno, f'["{node.slice.value}"]'))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in {"get", "pop", "setdefault"} and node.args
              and isinstance(node.args[0], ast.Constant) and node.args[0].value in MIRROR_FIELDS):
            found.append((node.lineno, f'.{node.func.attr}("{node.args[0].value}")'))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "getattr" and len(node.args) >= 2
              and isinstance(node.args[1], ast.Constant) and node.args[1].value in MIRROR_FIELDS):
            found.append((node.lineno, f'getattr(..., "{node.args[1].value}")'))
    return sorted(found)


def test_decision_logic_reads_no_legacy_scalar() -> None:
    offenders = []
    for path in sorted(LOGIC.rglob("*.py")):
        if path.name in ALLOWED:
            continue
        for line, form in mirror_reads(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(LOGIC.parent.parent)}:{line} {form}")
    assert not offenders, (
        "Decision logic must read the canonical vectors (fatigue_f / capacity_x), not the "
        "legacy scalar mirror:\n" + "\n".join(offenders)
    )


def test_allowlisted_files_still_exist() -> None:
    for name in ALLOWED:
        assert list(LOGIC.rglob(name)), f"stale allowlist entry: {name}"


@pytest.mark.parametrize(
    "source",
    [
        "x = state.f_met_systemic\n",
        'x = ctx.legacy["f_nm_central"]\n',
        'x = ctx.legacy.get("f_nm_peripheral", 0)\n',
        'x = getattr(state, "c_met_aerobic")\n',
        "f = lambda s: s.c_nm_force\n",
    ],
    ids=["attribute", "subscript", "get", "getattr", "lambda"],
)
def test_the_guard_catches_each_read_form(source: str) -> None:
    assert mirror_reads(source)


@pytest.mark.parametrize(
    "source",
    [
        "s.f_met_systemic = 1.0\n",                         # a write
        '_DriverRule("f_nm_central", lambda s: s.fatigue_f.cns)\n',  # an axis id
        '"""reads f_met_systemic in prose"""\n',
        "x = state.fatigue_f.metabolic\n",
    ],
    ids=["write", "axis-id", "docstring", "canonical"],
)
def test_the_guard_ignores_writes_ids_and_prose(source: str) -> None:
    assert not mirror_reads(source)
