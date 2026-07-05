"""Artifact JSON writers shared by the offline ML pipelines.

``write_artifact`` writes the pretty-printed JSON (trailing newline, UTF-8), creating the
parent directory if needed. ``write_validated_artifact`` first round-trips the payload
through the engine override loader so a schema drift fails loudly before anything is
written. Byte-identical to the per-pipeline copies these replace (plain: q3/q6/q9/q10;
validated: q2/dose_calibration).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_artifact(artifact: dict[str, Any], path: str | Path) -> Path:
    """Write ``artifact`` as indented JSON (with trailing newline) to ``path``."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return out


def write_validated_artifact(artifact: dict[str, Any], path: str | Path) -> Path:
    """Validate ``artifact`` against the override loader, then write it to ``path``."""
    from app.engine.parameter_overrides import load_override_artifact

    load_override_artifact(artifact)  # fail loudly if it drifts from the frozen schema
    return write_artifact(artifact, path)
