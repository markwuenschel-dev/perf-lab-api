"""ADR-0059 static guard — the seed snapshot is never read at runtime.

The single runtime authority for provisionality is the live per-axis
``CapacityConfidence`` variance. The immutable seed provenance snapshot
(``app.logic.seed_snapshot`` / ``AthleteProfile.initial_seed_by_axis``) is for
audit/analytics only. This dependency check fails if any runtime *engine compute*
module imports the snapshot module or reaches for its per-axis fields — that would
resurrect a parallel confidence authority. (The seed *writer*, ``state_service``, is
deliberately excluded — it persists the snapshot, it does not read it for gain.)
"""
from __future__ import annotations

from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"

# Runtime engine compute surface: the modules that turn state + evidence into a
# capacity update / dose / prescription. None may consult seed provenance.
_RUNTIME_ENGINE_MODULES: tuple[Path, ...] = (
    _APP / "logic" / "state_update_v0.py",
    _APP / "logic" / "dose_engine_v0.py",
    _APP / "logic" / "dose_routing.py",
    _APP / "logic" / "prescriber.py",
    _APP / "engine" / "state_bridge.py",
    _APP / "engine" / "parameters.py",
    _APP / "engine" / "simulate.py",
    *sorted((_APP / "logic" / "ekf").glob("*.py")),
    *sorted((_APP / "logic" / "mpc").glob("*.py")),
    *sorted((_APP / "logic" / "constraint_engine").glob("*.py")),
)

_FORBIDDEN_TOKENS = (
    "seed_snapshot",
    "initial_seed_by_axis",
    "initial_seed_confidence_by_axis",
    "initial_seed_source_by_axis",
)


def test_runtime_engine_modules_do_not_read_seed_snapshot() -> None:
    offenders: list[str] = []
    for module in _RUNTIME_ENGINE_MODULES:
        if not module.exists():
            continue
        src = module.read_text(encoding="utf-8")
        for token in _FORBIDDEN_TOKENS:
            if token in src:
                offenders.append(f"{module.relative_to(_APP.parent)} references {token!r}")
    assert not offenders, (
        "runtime engine modules must not read the seed snapshot (ADR-0059); "
        + "; ".join(offenders)
    )


def test_guard_covers_a_real_set_of_modules() -> None:
    # Guard against the check silently covering nothing (e.g. a path typo).
    existing = [m for m in _RUNTIME_ENGINE_MODULES if m.exists()]
    assert len(existing) >= 6
