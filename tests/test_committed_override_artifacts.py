"""Committed learned-override artifacts may not rot (PA-16).

The web build gates its generated files against drift (types.gen.ts, design tokens); the
committed engine override artifacts under app/engine/param_overrides/ had no equivalent
directory-level gate. Individual tests load a couple by namespace, but nothing asserts
that *every* committed artifact still parses, validates against the live loader schema,
and stays shadow-gated — so a newly committed artifact (or one broken by a schema change)
could slip in unguarded. The v0 placeholder, for instance, is never loaded by name because
load_namespace_override resolves to the highest version.

This iterates every committed artifact and pins two things:
  1. Drift — it still validates against app.engine.parameter_overrides.load_override_artifact.
  2. Safety — it is shadow_only, so it can never silently change a production decision
     (apply_parameter_overrides refuses a shadow_only artifact from a production caller).

Scope note: this is a schema/contract drift gate, not a reproduce-and-diff golden. The
q-model artifacts the audit referenced (app/ml/*_priors_v1.json) are not committed to the
repo — those trainers assert schema on a freshly trained artifact in their own tests — so
the only committed artifacts to guard are the three under param_overrides/.
"""
import pathlib

import pytest

from app.engine.parameter_overrides import load_override_artifact

_OVERRIDES_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "engine" / "param_overrides"
_COMMITTED = sorted(_OVERRIDES_DIR.glob("*.json"))


def test_param_overrides_directory_has_artifacts_to_guard() -> None:
    # Guards against a silent false-green: an empty glob would parametrize zero cases.
    assert _COMMITTED, f"no committed override artifacts found under {_OVERRIDES_DIR}"


@pytest.mark.parametrize("path", _COMMITTED, ids=lambda p: p.name)
def test_committed_override_artifact_loads_validates_and_is_shadow_only(
    path: pathlib.Path,
) -> None:
    # load_override_artifact dispatches on `kind` and raises on any schema violation.
    artifact = load_override_artifact(str(path))
    assert artifact["shadow_only"] is True, (
        f"{path.name} must be shadow_only — a committed prior that isn't shadow-gated could "
        f"reach a production decision"
    )
    # Filename convention: <namespace>_priors_*.json.
    assert artifact["namespace"] in path.name, (
        f"{path.name} declares namespace {artifact['namespace']!r}, which is not in its filename"
    )
