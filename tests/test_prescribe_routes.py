"""Route tests for GET /v1/next-session."""
import pytest

pytestmark = pytest.mark.asyncio


async def _register_and_login(client, email: str, password: str = "testpass99") -> str:
    reg = await client.post("/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text

    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


# ── /v1/next-session ──────────────────────────────────────────────────────────

async def test_next_session_without_auth_returns_401(http_client):
    resp = await http_client.get("/v1/next-session")
    assert resp.status_code == 401


async def test_next_session_returns_prescription_shape(http_client):
    """Fresh user gets a valid prescription (auto-init of S0 triggers)."""
    token = await _register_and_login(http_client, "prescribe_shape@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/next-session", headers=headers)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    for field in ("type", "focus", "rationale", "duration_min", "model_version", "exercises"):
        assert field in data, f"Missing field: {field}"


async def _prescribe(client, email: str, goal: str) -> dict:
    """Register a fresh athlete and fetch their next session for `goal`."""
    token = await _register_and_login(client, email)
    resp = await client.get(
        f"/v1/next-session?goal={goal}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _exercise_names(data: dict) -> list[str]:
    return [ex["name"] for ex in data["exercises"]]


async def test_next_session_auto_inits_state(http_client):
    """A fresh athlete has no state; next-session must auto-init one (ADR-0030 pipeline).

    Asserted through /v1/state-history, which reads athlete_states directly: the
    history is empty before the call and holds exactly one seeded state vector
    after it. Merely asserting 200 could not tell auto-init from a no-op.
    """
    token = await _register_and_login(http_client, "auto_init@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    before = await http_client.get("/v1/state-history", headers=headers)
    assert before.status_code == 200, before.text
    assert before.json() == [], "fresh athlete must start with no persisted state"

    resp = await http_client.get("/v1/next-session?goal=Strength", headers=headers)
    assert resp.status_code == 200, resp.text

    after = await http_client.get("/v1/state-history", headers=headers)
    assert after.status_code == 200, after.text
    states = after.json()
    assert len(states) == 1, f"expected exactly one auto-inited state, got {len(states)}"

    state = states[0]
    # The seeded vector is a real baseline, not an empty shell.
    assert state["timestamp"], "auto-inited state must carry a timestamp"
    for axis in ("capacity_x", "fatigue_f", "tissue_t", "capacity_confidence"):
        assert isinstance(state[axis], dict) and state[axis], f"{axis} not seeded"
    assert state["capacity_x"]["max_strength"] is not None


# ── goal dispatch ─────────────────────────────────────────────────────────────
#
# Each goal resolves to a canonical domain (GOAL_TO_DOMAIN, ADR-0038) which picks a
# distinct CandidateTemplate pool. The signatures below are the *observed* output for
# a freshly auto-inited athlete with no active block, no logged sessions and no weak
# points — i.e. goal is the only thing varying. They fail if goal dispatch is ignored,
# collapses to a default, or degenerates to an empty session.

async def test_next_session_goal_strength(http_client):
    data = await _prescribe(http_client, "strength_goal@test.com", "Strength")

    assert data["type"] == "Max Strength"
    assert "Back Squat" in _exercise_names(data)
    assert "strength" in data["rationale"].lower()


async def test_next_session_goal_hypertrophy(http_client):
    data = await _prescribe(http_client, "hyper_goal@test.com", "Hypertrophy")

    assert data["type"] == "High Volume Hypertrophy"
    # Hypertrophy routes to the accumulation pool, not the max-strength pool.
    names = _exercise_names(data)
    assert "Leg Press" in names
    assert "Back Squat" not in names


async def test_next_session_goal_power(http_client):
    data = await _prescribe(http_client, "power_goal@test.com", "Power")

    assert data["type"] == "Power Development"
    assert "Hang Power Clean" in _exercise_names(data)
    # Power is the shortest, freshest session — it must not be sized like volume work.
    assert data["duration_min"] == 50


async def test_next_session_goal_general(http_client):
    data = await _prescribe(http_client, "general_goal@test.com", "General")

    assert data["type"] == "General Physical Prep"
    # GPP is a full-body circuit, not a single-pattern block.
    names = _exercise_names(data)
    assert {"Goblet Squat", "Pull-Up", "Push-Up", "Farmer Carry"} <= set(names)


async def test_next_session_goals_do_not_collapse(http_client):
    """Different goals must yield materially different prescriptions.

    The per-goal tests above pin each signature; this pins the *relationship* — if
    goal dispatch were ever short-circuited to one default pool, every per-goal test
    would fail, but so would this one, and this one says why.
    """
    goals = ("Strength", "Hypertrophy", "Power", "General")
    by_goal = {
        g: await _prescribe(http_client, f"collapse_{g.lower()}@test.com", g)
        for g in goals
    }

    types = {g: d["type"] for g, d in by_goal.items()}
    assert len(set(types.values())) == len(goals), f"goals collapsed to same type: {types}"

    focuses = {g: d["focus"] for g, d in by_goal.items()}
    assert len(set(focuses.values())) == len(goals), f"goals collapsed to same focus: {focuses}"

    for g, d in by_goal.items():
        assert d["exercises"], f"{g} produced a degenerate (empty) session"

    exercise_sets = [frozenset(_exercise_names(d)) for d in by_goal.values()]
    assert len(set(exercise_sets)) == len(goals), (
        f"goals collapsed to same exercise selection: "
        f"{ {g: _exercise_names(d) for g, d in by_goal.items()} }"
    )


async def test_next_session_why_field_present(http_client):
    """The `why` field (PrescriptionExplanation) should be populated."""
    token = await _register_and_login(http_client, "why_field@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/next-session?goal=Strength", headers=headers)
    assert resp.status_code == 200

    why = resp.json().get("why")
    assert why is not None, "why field should be present in prescription"
    assert "state_drivers" in why
    assert "constraints_applied" in why


async def test_next_session_model_version_v03(http_client):
    token = await _register_and_login(http_client, "model_ver@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/next-session", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["model_version"] == "v0.3"


async def test_next_session_ignores_user_id_query_param(http_client):
    """Passing ?user_id=<other_user_id> must not escalate privileges.

    The route must return a valid prescription for user A (the authenticated
    caller) regardless of the user_id query param value.
    """
    # Register user A and user B
    token_a = await _register_and_login(http_client, "user_a_isolation@test.com")
    token_b = await _register_and_login(http_client, "user_b_isolation@test.com")

    # Resolve user B's id by calling /auth/me as user B
    me_resp = await http_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert me_resp.status_code == 200, me_resp.text
    user_b_id = me_resp.json()["id"]

    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Call next-session as user A, passing user B's id as the query param
    resp_with_param = await http_client.get(
        f"/v1/next-session?user_id={user_b_id}", headers=headers_a
    )
    assert resp_with_param.status_code == 200, resp_with_param.text

    data = resp_with_param.json()
    for field in ("type", "focus", "rationale", "duration_min", "model_version", "exercises"):
        assert field in data, f"Missing field: {field}"

    # Call next-session as user A without the query param — must also succeed
    resp_without_param = await http_client.get("/v1/next-session", headers=headers_a)
    assert resp_without_param.status_code == 200, resp_without_param.text

    data_plain = resp_without_param.json()
    for field in ("type", "focus", "rationale", "duration_min", "model_version", "exercises"):
        assert field in data_plain, f"Missing field: {field}"
