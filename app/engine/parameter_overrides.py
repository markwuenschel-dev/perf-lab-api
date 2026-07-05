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
from typing import Any, cast

from app.engine.parameters import EngineParameters

_OVERRIDES_DIR = Path(__file__).parent / "param_overrides"

# The recovery clearance modifier this slice targets. Production reads only
# {sleep, stress}; a learned artifact may extend to the wider wellness set.
_FATIGUE_AXES = {"cns", "muscular", "metabolic", "structural", "tendon", "grip"}
_RECOVERY_SIGNALS = {"sleep", "stress", "hrv", "rhr", "soreness", "mood"}

# Explicit artifact-kind discriminator. Every override artifact declares its ``kind``
# so the loader dispatches on it rather than sniffing which payload keys are present.
KIND_RECOVERY = "recovery_priors"
KIND_DOSE = "dose_overrides"

_REQUIRED_KEYS = {"kind", "version", "namespace", "shadow_only", "recovery_clearance_beta", "clip"}

# --- Dose-law calibration (additive path; app/ml/dose_calibration) --------------------
# A dose-calibration artifact carries an ``engine_overrides`` block naming EngineParameters
# dose fields to merge (weak population priors on the dose law). The names are validated
# against this whitelist so an artifact can never touch a non-dose parameter, and the merge
# is additive (partial dicts merge into the copy; unspecified keys keep their defaults).
_DOSE_SCALAR_FIELDS = {
    "dose_alpha", "dose_beta", "dose_gamma", "dose_rho",
    "dose_delta_sets_multiplier", "dose_delta_min_divisor", "dose_delta_cap", "dose_delta_floor",
    "dose_novelty_floor", "dose_w_phi_floor",
    "dose_human_factor_reference", "dose_human_factor_slope",
}
# Flat dict[str, float] fields, with the sub-keys each one is allowed to carry.
_DOSE_MAP_SUBKEYS: dict[str, set[str]] = {
    "dose_volume_weights": {"duration", "volume_load", "sets"},
    "dose_entry_volume_proxy_weights": {
        "volume_load", "duration_divisor", "distance_divisor", "sets_reps"
    },
}
# Nested dict[str, dict[str, float]] fields: outer modality keys -> six dose-axis multipliers.
_DOSE_SHAPE_MODALITIES = {"Running", "strength", "default"}
_DOSE_SHAPE_AXES = {"volume", "intensity", "density", "impact", "skill", "metabolic"}
_DOSE_NESTED_MAP_FIELDS = {"dose_shape_six_by_modality"}

_DOSE_FIELDS = _DOSE_SCALAR_FIELDS | set(_DOSE_MAP_SUBKEYS) | _DOSE_NESTED_MAP_FIELDS
_DOSE_REQUIRED_KEYS = {"kind", "version", "namespace", "shadow_only", "engine_overrides"}


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
    # Dispatch on the explicit ``kind`` discriminator: a dose-calibration artifact is
    # validated against the dose whitelist, a recovery artifact against the frozen
    # recovery schema. An absent/unknown kind is a hard error (no silent fallthrough).
    kind = artifact.get("kind")
    if kind == KIND_DOSE:
        _validate_dose(artifact)
    elif kind == KIND_RECOVERY:
        _validate(artifact)
    else:
        raise OverrideError(
            f"override artifact 'kind' must be {KIND_RECOVERY!r} or {KIND_DOSE!r}, got {kind!r}"
        )
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

    clip: dict[str, Any] = a["clip"]
    if not isinstance(clip, dict) or "min" not in clip or "max" not in clip:
        raise OverrideError("clip must be an object with 'min' and 'max'")
    lo, hi = float(clip["min"]), float(clip["max"])
    if not (0.0 < lo < hi):
        raise OverrideError(f"clip bounds must satisfy 0 < min < max, got min={lo} max={hi}")

    beta: dict[str, dict[str, Any]] = a["recovery_clearance_beta"]
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


def _validate_dose(a: dict[str, Any]) -> None:
    """Validate a dose-calibration artifact's ``engine_overrides`` block against the whitelist."""
    missing = _DOSE_REQUIRED_KEYS - a.keys()
    if missing:
        raise OverrideError(f"dose override artifact missing keys: {sorted(missing)}")
    if not isinstance(a["shadow_only"], bool):
        raise OverrideError("shadow_only must be a bool")

    eo: dict[str, Any] = a["engine_overrides"]
    if not isinstance(eo, dict) or not eo:
        raise OverrideError("engine_overrides must be a non-empty object")

    for field_name, value in eo.items():
        if field_name not in _DOSE_FIELDS:
            raise OverrideError(f"unknown / non-dose engine_overrides field: {field_name!r}")
        if field_name in _DOSE_SCALAR_FIELDS:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise OverrideError(f"engine_overrides[{field_name!r}] must be numeric")
        elif field_name in _DOSE_MAP_SUBKEYS:
            allowed = _DOSE_MAP_SUBKEYS[field_name]
            if not isinstance(value, dict):
                raise OverrideError(f"engine_overrides[{field_name!r}] must be an object")
            for k, v in cast("dict[str, Any]", value).items():
                if k not in allowed:
                    raise OverrideError(f"unknown key {k!r} for engine_overrides[{field_name!r}]")
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    raise OverrideError(f"engine_overrides[{field_name!r}][{k!r}] must be numeric")
        else:  # nested map: dose_shape_six_by_modality
            if not isinstance(value, dict):
                raise OverrideError(f"engine_overrides[{field_name!r}] must be an object")
            for mod, axes in cast("dict[str, Any]", value).items():
                if mod not in _DOSE_SHAPE_MODALITIES:
                    raise OverrideError(f"unknown modality {mod!r} for engine_overrides[{field_name!r}]")
                if not isinstance(axes, dict):
                    raise OverrideError(f"engine_overrides[{field_name!r}][{mod!r}] must be an object")
                for ax, mult in cast("dict[str, Any]", axes).items():
                    if ax not in _DOSE_SHAPE_AXES:
                        raise OverrideError(f"unknown dose axis {ax!r} for modality {mod!r}")
                    if not isinstance(mult, (int, float)) or isinstance(mult, bool):
                        raise OverrideError(f"engine_overrides[{field_name!r}][{mod!r}][{ax!r}] must be numeric")


def _merge_engine_overrides(merged: EngineParameters, eo: dict[str, Any]) -> None:
    """Additively merge a validated ``engine_overrides`` block into the (already-copied) params.

    ``merged`` is a deep copy, so mutating its dose dicts in place is non-invasive. Partial
    dicts merge key-by-key; fields/keys absent from the block keep their engine defaults.
    """
    for field_name, value in eo.items():
        if field_name in _DOSE_SCALAR_FIELDS:
            setattr(merged, field_name, float(value))
        elif field_name in _DOSE_MAP_SUBKEYS:
            target: dict[str, float] = getattr(merged, field_name)
            for k, v in value.items():
                target[k] = float(v)
        else:  # dose_shape_six_by_modality
            nested: dict[str, dict[str, float]] = getattr(merged, field_name)
            for mod, axes in value.items():
                dest = nested.setdefault(mod, {})
                for ax, mult in axes.items():
                    dest[ax] = float(mult)


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
    # Recovery path (unchanged): a learned per-axis beta map replaces that axis's weights.
    if "recovery_clearance_beta" in a:
        for axis, signals in a["recovery_clearance_beta"].items():
            merged.recovery_clearance_beta[axis] = {k: float(v) for k, v in signals.items()}
        merged.recovery_clearance_min = float(a["clip"]["min"])
        merged.recovery_clearance_max = float(a["clip"]["max"])
    # Dose path (additive): merge whitelisted dose fields onto the copy.
    if "engine_overrides" in a:
        _merge_engine_overrides(merged, a["engine_overrides"])
    return merged
