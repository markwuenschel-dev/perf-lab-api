"""INT-15 W1-A — structural contracts on who may load state permissively.

Two allowlists, enforced structurally rather than by review memory:

1. The temporary permissive loaders — every entry point that can hand a caller a legacy
   reconstruction as though it were canonical (`_PERMISSIVE_NAMES`) — are pinned to their
   CURRENT importers. **This list may only shrink.** It is the thing
   that makes the expand-contract intermediate state actually temporary — without it,
   "temporary" is a promise in a doc, and new callers quietly inherit the permissive
   default the strict design exists to remove. Slice 2D deletes these loaders when the
   list is empty.

2. The display-recovery loaders are pinned to surfaces proven display-only. Recovery is a
   capability that must be SELECTED, never inherited, so a decision consumer importing
   the display loader is a structural violation, not a code-review opinion.

See docs/superpowers/plans/2026-07-15-int-15-strict-state-loading.md.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

# Every module that references a permissive loader TODAY, derived by AST scan.
# DO NOT ADD TO THIS LIST. Each entry is removed as slice 2B/2C/S3/S4 reclassifies it.
PERMISSIVE_LOADER_ALLOWLIST: frozenset[str] = frozenset(
    {
        # AUD-C15 REMOVED `api/v1/history.py` — its state-history read now delegates to
        # `state_service.load_recent_states` behind the AthleteContextRepository seam, so the
        # route no longer imports a permissive loader directly. The permissive conversion is
        # consolidated in `state_service.py` (below), which INT-15 retires in 2D.
        "services/state_service.py",  # defines the wrappers; retired in 2D
        # C1 REMOVED `services/assessment_surface_service.py` (2B4) — strict: its variance ranks
        # the next benchmark, so it is recommendation authority, not display.
        # C1 REMOVED `services/benchmark_service.py` (2B3) — strict at the state-application
        # site; the two existence checks read `has_state` and decode nothing.
        # C1: `api/v1/simulate.py` was never listed because the guard only matched two names;
        # it reached `load_or_init_current_state`. It is strict as of C1 (capability
        # `projection`), so it is not listed now either.
        # 2C REMOVED `api/v1/history.py` and `services/dashboard_service.py` — display
        # recovery with provenance (DISPLAY_LOADER_ALLOWLIST below), and an unshowable row
        # refuses the surface (409) instead of being dropped.
        # S3 REMOVED `services/ekf_shadow_service.py` and `services/recovery_shadow_service.py`
        # — they read `try_load_current_state_for_shadow`: canonical state or a recorded skip,
        # never a reconstruction.
        # 2C REMOVED `services/onboarding_service.py` — strict: its twin summary reads only
        # live confidence, which a legacy recovery does not have; damaged state is reported
        # as `state_available=false`, never recovered and never initialized over.
        # 2B1 REMOVED `services/prescription_service.py` — both load-sizing sites are strict
        # as of that slice, and a decode failure is translated to CanonicalStateInvalid at
        # the service, then mapped to 409 by the global handler.
        # 2B2 REMOVED `services/readiness_service.py` — strict as of that slice. Readiness
        # is not display: it gates prescription, so it refuses rather than scoring an
        # athlete from a legacy reconstruction.
        # S4 REMOVED `scripts/repair_capacity_corruption.py` — it is strict as of this
        # slice. It was never the INT-15 forensic repair utility (that path,
        # `read_raw_state_for_repair`, is still unimplemented); it is the ADR-0055 capacity
        # job, and it MUTATES canonical state, so it is strict for the same reason
        # benchmark_service is. Its earlier entry here described the forensic tool by
        # mistake — an AST scan matched the word "repair" and the note inherited the design
        # doc's `repair utility | Raw forensic access` row.
    }
)

# Display recovery may be imported ONLY by surfaces proven unable to affect prescription,
# readiness, eligibility, benchmark selection, state mutation, or objective progress.
DISPLAY_LOADER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "services/state_service.py",  # defines the wrappers
        "services/dashboard_service.py",  # 2C: /v1/dashboard/readiness, returned with provenance
        "api/v1/history.py",  # 2C: /v1/state-history, every row carries its provenance
    }
)

# Every public entry point that can return a legacy reconstruction. Widened in C1: before it,
# only the first two were matched, so `load_or_init_current_state` (simulate.py) and
# `load_recent_state_snapshots` (history.py) reached permissive loading unguarded.
# `preview_baseline_state` is not here: it decodes a row it has just built, strictly.
_PERMISSIVE_NAMES = frozenset(
    {
        "unified_from_athlete_row",
        "load_current_state",
        "load_or_init_current_state",
        "load_recent_states",
    }
)
# `load_recent_state_snapshots` moved here in 2C: it now reads through display recovery and
# marks every row's provenance, so it is a display entry point, not a permissive one.
_DISPLAY_NAMES = frozenset(
    {
        "load_current_state_for_display",
        "reconstruct_legacy_state_for_display",
        "ReadOnlyStateView",
        "load_recent_state_snapshots",
    }
)


def _references(source: str, names: frozenset[str], filename: str = "<src>") -> bool:
    """Does `source` reference any of `names` in CODE (not prose)?

    Four forms carry the dependency: a bare name, a module attribute
    (`state_service.load_current_state`), a `from x import (name as alias)` of any layout, and
    `getattr(module, "name")`.

    A local definition excuses only a BARE-name reference to itself (`load_current_state()`
    inside the module that defines it). It never excuses the other three forms: a module that
    defines its own `load_current_state` and also calls `state_service.load_current_state` or
    imports it is still a caller (C1 review: that was a blind spot).
    """
    tree = ast.parse(source, filename=filename)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    for node in ast.walk(tree):
        referenced: str | None = None
        if isinstance(node, ast.Name):
            if node.id in defined:
                continue  # its own definition, called by bare name
            referenced = node.id
        elif isinstance(node, ast.Attribute):
            referenced = node.attr
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in names:
                    referenced = alias.name
                    break
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            referenced = node.args[1].value
        if referenced in names:
            return True
    return False


def _modules_referencing(names: frozenset[str]) -> set[str]:
    """Modules that reference any of `names` in CODE.

    AST, not regex over import lines. The regex version silently missed the two forms that
    actually carry this dependency — parenthesized multi-line `from x import (\\n name,\\n)`
    and module-attribute access (`state_service.load_current_state(...)`, how four services
    reach it) — while happily matching prose in a docstring. An allowlist that under-reports
    is worse than none: it reads green while the thing it guards leaks.

    A module that *defines* a name is not a caller of it, so definitions are excluded.
    """
    return {
        path.relative_to(APP).as_posix()
        for path in APP.rglob("*.py")
        if _references(path.read_text(encoding="utf-8"), names, filename=str(path))
    }


def test_permissive_loader_allowlist_may_only_shrink() -> None:
    """A NEW caller of the permissive loader is the failure this test exists to catch.

    If this fails with an unexpected module, do not add it here. Point that caller at
    `load_current_state_strict` (decision/mutation) or `load_current_state_for_display`
    (proven display-only). Adding it re-establishes permissive-by-default one import at a
    time, which is exactly how the seam got this way.
    """
    actual = _modules_referencing(_PERMISSIVE_NAMES)

    added = actual - PERMISSIVE_LOADER_ALLOWLIST
    assert not added, (
        f"New permissive-loader callers: {sorted(added)}. "
        "Wire them to load_current_state_strict or load_current_state_for_display instead."
    )


def test_permissive_allowlist_has_no_stale_entries() -> None:
    """Keeps the list honest as 2B/2C remove callers — a stale entry would let a caller
    silently return without tripping the shrink check."""
    actual = _modules_referencing(_PERMISSIVE_NAMES)

    stale = PERMISSIVE_LOADER_ALLOWLIST - actual
    assert not stale, f"Allowlist entries no longer import a permissive loader: {sorted(stale)}"


def test_no_decision_consumer_imports_the_display_loader() -> None:
    """Recovery must be selected, never inherited.

    Display recovery returns lossy reconstruction. A decision consumer reaching it — even
    to unwrap `.state` "just for a chart" — puts unmarked degraded data one refactor away
    from sizing an athlete's loads.
    """
    actual = _modules_referencing(_DISPLAY_NAMES)

    violations = actual - DISPLAY_LOADER_ALLOWLIST
    assert not violations, (
        f"Non-display modules importing display recovery: {sorted(violations)}. "
        "Only surfaces proven unable to affect prescription, readiness, eligibility, "
        "benchmark selection, state mutation, or objective progress may import it."
    )


# --- The guard must catch what it guards (C1) --------------------------------------------
# A scanner that under-reports reads green while the thing it guards leaks, so each form a
# forbidden caller could take is fed to it directly, for every permissive name.

_FORBIDDEN_FORMS = {
    "from_import": "from app.services.state_service import {n}\n",
    "multiline_alias": "from app.services.state_service import (\n    {n} as _x,\n)\n",
    "module_attribute": "from app.services import state_service\nstate_service.{n}(db, 1)\n",
    "getattr_string": "from app.services import state_service\ngetattr(state_service, '{n}')\n",
}


@pytest.mark.parametrize("form", sorted(_FORBIDDEN_FORMS))
@pytest.mark.parametrize("name", sorted(_PERMISSIVE_NAMES))
def test_the_guard_catches_every_form_of_a_forbidden_caller(name: str, form: str) -> None:
    assert _references(_FORBIDDEN_FORMS[form].format(n=name), _PERMISSIVE_NAMES), (name, form)


@pytest.mark.parametrize(
    "source",
    [
        '"""Prose naming load_current_state is not a caller."""\n',
        "from app.services.state_service import load_current_state_strict\n",
        "from app.services.state_service import load_or_init_current_state_strict\n",
        "from app.services import state_service\nstate_service.has_state(db, 1)\n",
        "async def load_current_state():\n    return None\n",
        "def load_current_state():\n    return load_current_state()\n",
    ],
    ids=["docstring", "strict", "strict-init", "existence", "definition", "own-recursion"],
)
def test_the_guard_does_not_flag_strict_or_prose(source: str) -> None:
    assert not _references(source, _PERMISSIVE_NAMES)


@pytest.mark.parametrize(
    "module",
    ["services/benchmark_service.py", "services/assessment_surface_service.py", "api/v1/simulate.py"],
)
def test_c1_decision_paths_reference_no_permissive_loader(module: str) -> None:
    """The three C1 callers, by file: none of them may reach a permissive entry point."""
    path = APP / module
    assert not _references(path.read_text(encoding="utf-8"), _PERMISSIVE_NAMES, str(path))


# --- Inside the allowlisted module, per reference (C1 review) -----------------------------
# The module allowlist above cannot see a NEW permissive decode added inside state_service.py —
# that is how process_new_workout's head read went unnoticed. A per-function set is not enough
# either: process_new_workout legitimately decodes rows it wrote itself, so reverting its head
# read would add a reference without adding a function. So the number of permissive references
# is pinned per scope. ONLY DECREASE THESE. 2D empties the map.
STATE_SERVICE_PERMISSIVE_REFERENCES: dict[str, int] = {
    "stage_baseline_state": 1,  # decodes the row it just built
    "load_current_state": 1,  # the permissive wrappers themselves (2C/2D)
    "load_recent_states": 1,
    "load_or_init_current_state": 2,  # its own decode, and the decoder it names for init
    # Rows it wrote itself or already decoded strictly — the record-only head, the tail-replay
    # head, the committed new row, the post-e1RM re-read. The head READ is strict as of C1.
    "process_new_workout": 4,
}


def _permissive_reference_counts(path: Path, names: frozenset[str]) -> dict[str, int]:
    """Permissive-name references per top-level function; anything else counts as <module>.

    Counts code references only (names, attributes, import aliases, getattr strings), and every
    scope including class bodies and module-level statements, so an alias like
    `_dec = unified_from_athlete_row` or `import ... as _d` at module level is seen too.
    Known limits of counting: a count-neutral swap of one permissive name for another, and a
    computed `getattr` string, are not seen.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    counts: dict[str, int] = {}

    def hits(node: ast.AST, own_name: str | None) -> int:
        n = 0
        for x in ast.walk(node):
            ref = None
            if isinstance(x, ast.Name):
                if x.id == own_name:
                    continue  # recursion by bare name; never excuses the forms below
                ref = x.id
            elif isinstance(x, ast.Attribute):
                ref = x.attr
            elif isinstance(x, ast.alias):
                ref = x.name
            elif (
                isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                and x.func.id == "getattr" and len(x.args) >= 2
                and isinstance(x.args[1], ast.Constant) and isinstance(x.args[1].value, str)
            ):
                ref = x.args[1].value
            if ref in names:
                n += 1
        return n

    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            key, n = stmt.name, hits(stmt, stmt.name)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            # A plain import is not a use: every use is counted where it occurs. A RENAMED
            # import is different — its uses are under the new name, which nothing counts — so
            # the rename itself is the counted (and therefore failing) reference.
            key = "<module>"
            n = sum(1 for a in stmt.names if a.asname and a.name in names)
        else:
            key, n = "<module>", hits(stmt, None)
        if n:
            counts[key] = counts.get(key, 0) + n
    return counts


def test_permissive_decodes_inside_state_service_are_pinned_per_reference() -> None:
    actual = _permissive_reference_counts(APP / "services" / "state_service.py", _PERMISSIVE_NAMES)
    assert actual == STATE_SERVICE_PERMISSIVE_REFERENCES, (
        f"actual {actual} != pinned {STATE_SERVICE_PERMISSIVE_REFERENCES}. A new permissive "
        "decode in state_service.py must be strict instead; a removed one lowers its count."
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("async def f(row):\n    return unified_from_athlete_row(row)\n", {"f": 1}),
        ("_dec = unified_from_athlete_row\n", {"<module>": 1}),
        ("class C:\n    def m(self, r):\n        return load_current_state(r)\n", {"<module>": 1}),
        ("from app.engine.state_bridge import unified_from_athlete_row\n", {}),
        ("from app.engine.state_bridge import unified_from_athlete_row as _d\n",
         {"<module>": 1}),
        ("async def load_current_state(db, uid):\n    return None\n", {}),
    ],
    ids=["function", "module-alias", "class-method", "import-only", "renamed-import",
         "definition"],
)
def test_the_reference_counter_sees_every_scope(
    tmp_path: Path, source: str, expected: dict[str, int]
) -> None:
    path = tmp_path / "m.py"
    path.write_text(source, encoding="utf-8")
    assert _permissive_reference_counts(path, _PERMISSIVE_NAMES) == expected


@pytest.mark.parametrize(
    "source",
    [
        # A local definition of the same name must not hide a real dependency (C1 review).
        "from app.services import state_service\n"
        "async def load_current_state(db, u):\n"
        "    return await state_service.load_current_state(db, u)\n",
        "from app.services.state_service import load_current_state as real\n"
        "def load_current_state():\n    pass\n",
        "def f(row):\n"
        "    def unified_from_athlete_row(r):\n        return r\n"
        "    return state_bridge.unified_from_athlete_row(row)\n",
        "class Repo:\n"
        "    def load_recent_states(self):\n"
        "        return getattr(state_service, 'load_recent_states')()\n",
    ],
    ids=["attribute-beside-local-def", "import-beside-local-def", "attribute-beside-nested-def",
         "getattr-beside-method"],
)
def test_a_local_definition_never_hides_a_real_dependency(source: str) -> None:
    assert _references(source, _PERMISSIVE_NAMES)


def test_the_counter_does_not_excuse_an_attribute_named_like_its_function(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(
        "async def load_current_state(db, u):\n"
        "    return await state_service.load_current_state(db, u)\n",
        encoding="utf-8",
    )
    assert _permissive_reference_counts(path, _PERMISSIVE_NAMES) == {"load_current_state": 1}
