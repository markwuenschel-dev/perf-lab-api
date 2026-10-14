"""The sync service must not know any concrete provider.

`app/integrations/base.py` promises "the sync service never imports a concrete provider".
That was false: `wearable_service` imported `OuraAdapter` and resolved it through
`if provider == "oura": ... else raise`, so adding Garmin meant editing the shared service.

The AST guard below is the point of this file. A registry that nothing enforces drifts back
the first time someone reaches for a concrete adapter "just here"; the promise in base.py is
only worth writing down if a test fails when it stops being true.
"""

import ast
from pathlib import Path

import pytest

from app.integrations.base import WearableAdapter
from app.integrations.oura import OuraAdapter
from app.integrations.registry import (
    UnknownWearableProvider,
    adapter_for,
    is_supported,
    register,
    supported_providers,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVICE = _REPO_ROOT / "app" / "services" / "wearable_service.py"
#: Every shared service module. The guard once read only wearable_service.py, so the OAuth
#: transaction (wearable_oauth.py) imported OuraAuthError past it (review, 2026-10-14).
_SHARED = sorted((_REPO_ROOT / "app" / "services").glob("*.py"))

#: The package holding concrete adapters. `base` and `registry` are the seam itself.
_CONCRETE_ADAPTER_MODULES = {"oura"}


def _imported_modules(source: str) -> set[str]:
    """Every module path this file imports, however it spells the import."""
    tree = ast.parse(source)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


# ── the guard ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("module", _SHARED, ids=lambda p: p.name)
def test_no_shared_service_imports_a_concrete_provider(module: Path) -> None:
    """base.py's own promise, made checkable, for every module in app/services.

    If this fails, a provider name has leaked back into shared code and the next integration
    has to edit it — which is the coupling the adapter Protocol exists to prevent. A provider's
    refusal reaches shared code as ``base.ProviderRejected``, never as the provider's own error.
    """
    imported = _imported_modules(module.read_text(encoding="utf-8"))
    leaked = {
        mod
        for mod in imported
        if mod.startswith("app.integrations.")
        and mod.rsplit(".", 1)[-1] in _CONCRETE_ADAPTER_MODULES
    }

    assert not leaked, (
        f"{module.name} imports concrete provider module(s) {sorted(leaked)}. "
        "Resolve providers through app.integrations.registry.adapter_for, and catch "
        "app.integrations.base.ProviderRejected rather than a provider's own exception."
    )


def test_the_guard_is_not_vacuous() -> None:
    names = {p.name for p in _SHARED}
    assert {"wearable_service.py", "wearable_oauth.py"} <= names
    assert "adapter_for" in _SERVICE.read_text(encoding="utf-8")


@pytest.mark.parametrize("module", _SHARED, ids=lambda p: p.name)
def test_no_provider_branching_in_shared_services(module: Path) -> None:
    """The `if provider == "oura"` ladder must not come back in another shape."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    known_providers = {"oura", "garmin", "whoop", "fitbit"}

    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        for comparator in node.comparators
        if isinstance(comparator, ast.Constant) and comparator.value in known_providers
    ]

    assert not offenders, (
        f"{module.name} branches on a provider name at line(s) "
        f"{', '.join(str(n) for n in offenders)}; register an adapter instead"
    )


# ── the neutral rejection ─────────────────────────────────────────────────────


async def test_oura_translates_a_token_refusal_into_the_neutral_rejection(monkeypatch) -> None:
    import httpx

    from app.integrations import oura
    from app.integrations.base import ProviderRejected

    refused = httpx.MockTransport(lambda request: httpx.Response(400, text="invalid_grant"))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(oura.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=refused, **kw))
    adapter = OuraAdapter()
    monkeypatch.setattr(adapter, "client_id", "cid")
    monkeypatch.setattr(adapter, "client_secret", "secret")
    with pytest.raises(ProviderRejected):
        await adapter.exchange_code("c", code_verifier="v" * 43, redirect_uri="https://x.test/cb")


async def test_a_local_oura_failure_is_not_a_provider_refusal(monkeypatch) -> None:
    """Missing credentials never reached Oura: shared code must not record it as Oura's 'no'."""
    from app.integrations import oura
    from app.integrations.base import ProviderRejected

    adapter = OuraAdapter()
    monkeypatch.setattr(adapter, "client_secret", "")
    with pytest.raises(oura.OuraAuthError) as excinfo:
        await adapter.exchange_code("c", code_verifier="v" * 43, redirect_uri="https://x.test/cb")
    assert not isinstance(excinfo.value, ProviderRejected)


# ── registry behaviour ────────────────────────────────────────────────────────


def test_oura_is_registered_and_resolves() -> None:
    assert is_supported("oura")
    assert "oura" in supported_providers()
    assert isinstance(adapter_for("oura"), OuraAdapter)


def test_the_resolved_adapter_satisfies_the_protocol() -> None:
    """Registration must not be able to smuggle in something that is not an adapter."""
    assert isinstance(adapter_for("oura"), WearableAdapter)


def test_an_unknown_provider_raises_rather_than_defaulting(monkeypatch) -> None:
    """Falling back to a default would attribute one vendor's data to another.

    `WellnessSample.source` is now load-bearing — it selects the per-signal authority and
    the baseline — so a silent default would corrupt readiness, not just mislabel a row.
    """
    with pytest.raises(UnknownWearableProvider) as excinfo:
        adapter_for("not_a_real_provider")

    assert "not_a_real_provider" in str(excinfo.value)
    assert excinfo.value.known == supported_providers()


def test_the_error_names_what_is_registered() -> None:
    """So the failure tells you what to do, not just that you were wrong."""
    with pytest.raises(UnknownWearableProvider, match="oura"):
        adapter_for("garmin")


def test_a_new_provider_needs_no_edit_to_shared_code(monkeypatch) -> None:
    """The whole point of the registry: registration is the only step.

    Registers a fake adapter and resolves it, without touching wearable_service.
    """
    monkeypatch.setitem(
        __import__("app.integrations.registry", fromlist=["_FACTORIES"])._FACTORIES,
        "fake_provider",
        lambda: _FakeAdapter(),
    )

    resolved = adapter_for("fake_provider")

    assert resolved.provider == "fake_provider"
    assert is_supported("fake_provider")


def test_a_mismatched_registration_is_rejected(monkeypatch) -> None:
    """A registry keyed on one slug returning an adapter that stamps another would write
    wellness rows under the wrong source — which now changes readiness."""
    monkeypatch.setitem(
        __import__("app.integrations.registry", fromlist=["_FACTORIES"])._FACTORIES,
        "mislabelled",
        lambda: _FakeAdapter(),  # reports "fake_provider", not "mislabelled"
    )

    with pytest.raises(RuntimeError, match="mislabelled"):
        adapter_for("mislabelled")


def test_registering_twice_replaces_rather_than_duplicating(monkeypatch) -> None:
    """So a test swapping in a fake need not know whether the real one registered first."""
    registry = __import__("app.integrations.registry", fromlist=["_FACTORIES"])
    monkeypatch.setattr(registry, "_FACTORIES", dict(registry._FACTORIES))

    register("dup", lambda: _FakeAdapter("dup"))
    register("dup", lambda: _FakeAdapter("dup"))

    assert supported_providers().count("dup") == 1


class _FakeAdapter:
    """Minimal stand-in; only `provider` is exercised by these tests."""

    def __init__(self, provider: str = "fake_provider") -> None:
        self.provider = provider

    def build_authorize_url(self, state: str, *, code_challenge: str, redirect_uri: str) -> str:  # pragma: no cover - unused here
        return f"https://example.invalid/auth?state={state}"

    async def exchange_code(self, code: str, *, code_verifier: str, redirect_uri: str):  # pragma: no cover - unused here
        raise NotImplementedError

    async def refresh_tokens(self, refresh_token: str):  # pragma: no cover - unused here
        raise NotImplementedError

    async def fetch_daily_wellness(self, access_token, start, end):  # pragma: no cover
        return []
