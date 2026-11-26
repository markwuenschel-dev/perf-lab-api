"""The build SHA's path from build-arg to shadow row must use one name end to end.

``dose_model_shadow_service`` reads ``BUILD_SHA_ENV`` from the process environment. The
Dockerfile turns a build-arg of that name into the image's ENV. If the two drift,
``code_version`` goes silently empty on rows. Nothing else would notice, because the service
treats a missing value as "unknown" by design.
"""

from __future__ import annotations

from pathlib import Path

from app.services.dose_model_shadow_service import BUILD_SHA_ENV

ROOT = Path(__file__).resolve().parents[1]


def test_the_dockerfile_bakes_the_build_arg_into_the_image_env() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f"ARG {BUILD_SHA_ENV}" in text
    assert f"ENV {BUILD_SHA_ENV}=${{{BUILD_SHA_ENV}}}" in text
