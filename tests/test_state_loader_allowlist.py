"""INT-15 W1-A — structural contracts on who may load state permissively.

Two allowlists, enforced structurally rather than by review memory:

1. The temporary permissive loaders (`unified_from_athlete_row`, `load_current_state`)
   are pinned to their CURRENT importers. **This list may only shrink.** It is the thing
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

APP = Path(__file__).resolve().parents[1] / "app"

# Every module that references a permissive loader TODAY, derived by AST scan.
# DO NOT ADD TO THIS LIST. Each entry is removed as slice 2B/2C/S3/S4 reclassifies it.
PERMISSIVE_LOADER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "api/v1/history.py",  # 2C: -> load_current_state_for_display
        "services/state_service.py",  # defines the wrappers; retired in 2D
        "services/assessment_surface_service.py",  # 2B4: strict unless proven display-only
        "services/benchmark_service.py",  # 2B3: strict, gated on the e1RM transaction proof
        "services/dashboard_service.py",  # 2C: display recovery
        "services/ekf_shadow_service.py",  # S3: skip adapter
        "services/onboarding_service.py",  # 2B4: strict, or explicit initialization
        # 2B1 REMOVED `services/prescription_service.py` — both load-sizing sites are strict
        # as of that slice, and a decode failure is translated to CanonicalStateInvalid at
        # the service, then mapped to 409 by the global handler.
        # 2B2 REMOVED `services/readiness_service.py` — strict as of that slice. Readiness
        # is not display: it gates prescription, so it refuses rather than scoring an
        # athlete from a legacy reconstruction.
        "services/recovery_shadow_service.py",  # S3: skip adapter
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
# Empty until 2C wires dashboard/history.
DISPLAY_LOADER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "services/state_service.py",  # defines the wrapper
    }
)

_PERMISSIVE_NAMES = frozenset({"unified_from_athlete_row", "load_current_state"})
_DISPLAY_NAMES = frozenset(
    {"load_current_state_for_display", "reconstruct_legacy_state_for_display", "ReadOnlyStateView"}
)


def _modules_referencing(names: frozenset[str]) -> set[str]:
    """Modules that reference any of `names` in CODE.

    AST, not regex over import lines. The regex version silently missed the two forms that
    actually carry this dependency — parenthesized multi-line `from x import (\\n name,\\n)`
    and module-attribute access (`state_service.load_current_state(...)`, how four services
    reach it) — while happily matching prose in a docstring. An allowlist that under-reports
    is worse than none: it reads green while the thing it guards leaks.

    A module that *defines* a name is not a caller of it, so definitions are excluded.
    """
    hits: set[str] = set()
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        for node in ast.walk(tree):
            referenced: str | None = None
            if isinstance(node, ast.Name):
                referenced = node.id
            elif isinstance(node, ast.Attribute):
                referenced = node.attr
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in names:
                        referenced = alias.name
                        break
            if referenced in names and referenced not in defined:
                hits.add(path.relative_to(APP).as_posix())
                break
    return hits


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
