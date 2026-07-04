"""Load offline-learned parameter priors and merge them onto EngineParameters.

Non-invasive by construction: ``apply_parameter_overrides`` returns a deep COPY —
``default_parameters()`` and the production engine are never mutated. Learned priors
live as versioned JSON artifacts under ``param_overrides/``. An artifact flagged
``shadow_only`` can only be applied by a caller that explicitly opts in
(``allow_shadow=True``) — i.e. the recovery shadow service — so a learned prior can
never silently change a production decision until it has been validated and promoted.

This is the concrete form of the ``parameters.py`` "future: load from DB or YAML" hook.
The v1 artifact seeds population-level *weak priors* (never per-athlete personalization).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app.engine.parameters import EngineParameters

_OVERRIDES_DIR = Path(__file__).parent / "param_overrides"

# The recovery clearance modifier this slice targets. Production reads only
# {sleep, stress}; a learned artifact may extend to the wider wellness set.
_FATIGUE_AXES = {"cns", "muscular", "metabolic", "structural", "tendon", "grip"}
_RECOVERY_SIGNALS = {"sleep", "stress", "hrv", "rhr", "soreness", "mood"}

_REQUIRED_KEYS = {"version", "namespace", "shadow_only", "recovery_clearance_beta", "clip"}


class OverrideError(ValueError):
    """A learned-override artifact is malformed, out of bounds, or misapplied."""


def load_override_artifact(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Parse + validate a learned-override artifact (path or already-loaded dict)."""
    if isinstance(source, dict):
        artifact = source
    else:
        path = Path(source)
        if not path.is_absolute():
            path = _OVERRIDES_DIR / path
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as e:
            raise OverrideError(f"override artifact not found: {path}") from e
        except json.JSONDecodeError as e:
            raise OverrideError(f"override artifact is not valid JSON: {path}") from e
    _validate(artifact)
    return artifact


def load_namespace_override(namespace: str, *, directory: Path | None = None) -> dict[str, Any] | None:
    """Highest-versioned artifact for a namespace (``<namespace>_priors_*.json``), or None."""
    directory = directory or _OVERRIDES_DIR
    if not directory.is_dir():
        return None
    candidates = sorted(directory.glob(f"{namespace}_priors_*.json"))
    if not candidates:
        return None
    return load_override_artifact(candidates[-1])


def _validate(a: dict[str, Any]) -> None:
    missing = _REQUIRED_KEYS - a.keys()
    if missing:
        raise OverrideError(f"override artifact missing keys: {sorted(missing)}")
    if not isinstance(a["shadow_only"], bool):
        raise OverrideError("shadow_only must be a bool")

    clip = a["clip"]
    if not isinstance(clip, dict) or "min" not in clip or "max" not in clip:
        raise OverrideError("clip must be an object with 'min' and 'max'")
    lo, hi = float(clip["min"]), float(clip["max"])
    if not (0.0 < lo < hi):
        raise OverrideError(f"clip bounds must satisfy 0 < min < max, got min={lo} max={hi}")

    beta = a["recovery_clearance_beta"]
    if not isinstance(beta, dict) or not beta:
        raise OverrideError("recovery_clearance_beta must be a non-empty object")
    for axis, signals in beta.items():
        if axis not in _FATIGUE_AXES:
            raise OverrideError(f"unknown fatigue axis in recovery_clearance_beta: {axis!r}")
        if not isinstance(signals, dict):
            raise OverrideError(f"recovery_clearance_beta[{axis!r}] must be an object")
        for sig, w in signals.items():
            if sig not in _RECOVERY_SIGNALS:
                raise OverrideError(f"unknown recovery signal {sig!r} for axis {axis!r}")
            if not isinstance(w, (int, float)):
                raise OverrideError(f"recovery_clearance_beta[{axis!r}][{sig!r}] must be numeric")


def apply_parameter_overrides(
    params: EngineParameters,
    artifact: str | Path | dict[str, Any],
    *,
    allow_shadow: bool = False,
) -> EngineParameters:
    """Return a COPY of ``params`` with the learned recovery priors merged in.

    ``params`` is never mutated. A ``shadow_only`` artifact raises unless the caller
    passes ``allow_shadow=True`` (only the recovery shadow service does), so learned
    priors cannot leak into a production decision path. The learned per-axis beta map
    REPLACES that axis's signal weights (the artifact fully specifies the learned form);
    axes absent from the artifact keep their defaults. The clip bounds come from the
    artifact so a learned multiplier can never exceed the reviewed [min, max] envelope.
    """
    a = load_override_artifact(artifact)
    if a["shadow_only"] and not allow_shadow:
        raise OverrideError(
            f"artifact {a['version']!r} is shadow_only; a production caller must not apply it "
            "(only the shadow service passes allow_shadow=True)"
        )

    merged = copy.deepcopy(params)
    for axis, signals in a["recovery_clearance_beta"].items():
        merged.recovery_clearance_beta[axis] = {k: float(v) for k, v in signals.items()}
    merged.recovery_clearance_min = float(a["clip"]["min"])
    merged.recovery_clearance_max = float(a["clip"]["max"])
    return merged
