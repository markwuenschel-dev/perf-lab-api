"""Unit tests for the current-state loaders — no DB required.

These characterize the fetch -> convert (-> auto-init) branch logic that was
previously hand-reassembled at six call sites. The repository seam, the
row->vector conversion, and initialize_athlete_state are monkeypatched, so the
branch behavior is verified locally without a live Postgres.
"""

from app.services import state_service


def _install(monkeypatch, *, row, init_vector=None):
    """Patch the repo seam, conversion, and init; return a call-counter dict."""
    calls = {"init": 0}

    class _FakeRepo:
        def __init__(self, db):
            self.db = db

        async def get_latest_state(self, user_id):
            return row

    async def _fake_init(db, user_id):
        calls["init"] += 1
        return init_vector

    monkeypatch.setattr(state_service, "AthleteContextRepository", _FakeRepo)
    monkeypatch.setattr(
        state_service, "unified_from_athlete_row", lambda r: ("converted", r)
    )
    monkeypatch.setattr(state_service, "initialize_athlete_state", _fake_init)
    return calls


# --- load_current_state: read-or-None -------------------------------------------


async def test_load_current_state_present_converts_row(monkeypatch):
    row = object()
    calls = _install(monkeypatch, row=row)
    result = await state_service.load_current_state(object(), user_id=1)
    assert result == ("converted", row)
    assert calls["init"] == 0  # never auto-inits


async def test_load_current_state_absent_returns_none(monkeypatch):
    calls = _install(monkeypatch, row=None)
    result = await state_service.load_current_state(object(), user_id=1)
    assert result is None
    assert calls["init"] == 0  # never auto-inits on the read path


# --- load_or_init_current_state: read-or-init -----------------------------------


async def test_load_or_init_present_converts_row(monkeypatch):
    row = object()
    calls = _install(monkeypatch, row=row, init_vector=("init", None))
    result = await state_service.load_or_init_current_state(object(), user_id=1)
    assert result == ("converted", row)
    assert calls["init"] == 0  # existing state -> no init


async def test_load_or_init_absent_initializes_once(monkeypatch):
    sentinel = ("init", None)
    calls = _install(monkeypatch, row=None, init_vector=sentinel)
    result = await state_service.load_or_init_current_state(object(), user_id=1)
    assert result is sentinel  # returns init's vector directly (no redundant re-fetch)
    assert calls["init"] == 1


# --- call-site wiring (DB-free) --------------------------------------------------
# The route/service DB-integration tests skip locally (async_db event-loop bug), so
# this exercises the swapped call site's no-state branch directly, without Postgres.


async def test_dashboard_readiness_payload_no_state_branch(monkeypatch):
    from app.services import dashboard_service

    async def _none(db, user_id):
        return None

    monkeypatch.setattr(dashboard_service, "load_current_state", _none)
    state, payload = await dashboard_service.readiness_payload(object(), user_id=1)
    assert state is None
    assert payload == {"note": "no_athlete_state"}
