"""The boot guard resolves the Alembic head independent of the process CWD (PA-14).

The guard used to call ``Config("alembic.ini")`` with a relative path, so booting from
any directory other than the repo root raised inside the head lookup — and in production
that surfaced as a hard "could not verify the schema" boot failure, indistinguishable
from a genuinely stale schema. The resolver now derives both the ini path and
script_location from the module's own location. This test pins that: from an unrelated
working directory it must still find the real head.
"""
import pytest

from app.main import _ALEMBIC_DIR, _expected_alembic_head


def test_expected_alembic_head_resolves_from_an_unrelated_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # A directory with no alembic.ini — exactly what broke the old relative Config().
    monkeypatch.chdir(tmp_path)

    head = _expected_alembic_head()

    assert isinstance(head, str) and head, "must resolve the on-disk migration head from any CWD"
    # Sanity: the migrations directory the resolver points at actually exists and is the
    # repo's, not something relative to the temp CWD.
    assert _ALEMBIC_DIR.is_dir()
    assert (_ALEMBIC_DIR / "versions").is_dir()
