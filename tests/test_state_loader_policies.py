"""Design guard: state_service exposes exactly the reviewed current-state load policies (AUD-C17).

``state_service`` intentionally offers five *purpose-named* current-state loaders rather than
a single ``load_state(mode=...)``. Each encodes a load-bearing (strictness × on-missing)
decision the design deliberately keeps out of a caller-selectable argument — collapsing them
into a mode flag would make permissiveness caller-selectable and recreate the
authority-selection problem the split removed.

This guard doesn't force a refactor; it makes changing that surface an explicit architecture
decision. A SIXTH current-state loading policy (a new ``load_*current_state*`` entry point)
fails CI until reviewed into ``CURRENT_STATE_LOADERS`` here, and collapsing/removing the five
(e.g. into a mega-loader) fails too. Mechanical internals may still be shared privately.

Scope: only the current-state *policy* family (names containing ``current_state``). Multi-row
history loaders (e.g. ``load_recent_states``) are a different capability and out of scope.
"""
import ast
from pathlib import Path

STATE_SERVICE = Path(__file__).resolve().parents[1] / "app" / "services" / "state_service.py"

# The reviewed set of public current-state load policies. Adding a sixth is an explicit
# architecture decision (update this set with review); do NOT collapse into load_state(mode=).
CURRENT_STATE_LOADERS = frozenset(
    {
        "load_current_state",
        "load_or_init_current_state",
        "load_current_state_strict",
        "load_or_init_current_state_strict",
        "load_current_state_for_display",
    }
)


def _public_current_state_loaders() -> set[str]:
    """Top-level public ``load_*current_state*`` functions in state_service."""
    tree = ast.parse(STATE_SERVICE.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("load")
        and not node.name.startswith("_")
        and "current_state" in node.name
    }


def test_current_state_load_policies_are_the_reviewed_set() -> None:
    actual = _public_current_state_loaders()

    added = actual - CURRENT_STATE_LOADERS
    assert not added, (
        f"new public current-state load policy in state_service: {sorted(added)}. A sixth "
        "current-state loader is an explicit architecture decision — do not collapse policy "
        "selection into a load_state(mode=...) argument; review it and add it to "
        "CURRENT_STATE_LOADERS here (AUD-C17)."
    )
    removed = CURRENT_STATE_LOADERS - actual
    assert not removed, (
        f"reviewed current-state loader(s) missing (renamed, removed, or collapsed): "
        f"{sorted(removed)}. That surface change needs an explicit architecture decision — "
        "do not fold these purpose-named policies into a mode argument (AUD-C17)."
    )
