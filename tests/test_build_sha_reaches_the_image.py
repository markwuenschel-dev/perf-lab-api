"""The build SHA's path from deploy to shadow row must use one name end to end.

``dose_model_shadow_service`` reads ``BUILD_SHA_ENV`` from the process environment. The
Dockerfile turns a build-arg of that name into the image's ENV, and both deploy scripts pass
it. If any of the four drifts, ``code_version`` goes silently empty again on production rows.
Nothing else would notice, because the service treats a missing value as "unknown" by design.
"""

from __future__ import annotations

from pathlib import Path

from app.services.dose_model_shadow_service import BUILD_SHA_ENV

ROOT = Path(__file__).resolve().parents[1]


def test_the_dockerfile_bakes_the_build_arg_into_the_image_env() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f"ARG {BUILD_SHA_ENV}" in text
    assert f"ENV {BUILD_SHA_ENV}=${{{BUILD_SHA_ENV}}}" in text


def test_both_deploy_scripts_pass_the_build_arg() -> None:
    for script in ("deploy.sh", "deploy.ps1"):
        text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert f"--build-arg {BUILD_SHA_ENV}=" in text, script
        # `up --build` would rebuild WITHOUT the arg and bake an empty SHA over it.
        assert "up -d --build" not in text, script
