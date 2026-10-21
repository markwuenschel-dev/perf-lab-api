"""The C1 sweep — heads by the repository's ordering, counts that partition, no payloads.

`classify()` itself is pinned in `test_audit_engine_state_classify.py`. This file pins what
the sweep reports and recommends: how heads are chosen, that groups are counted apart, that
athlete absence is not counted as damage, and that no verdict proposes reconstructing canonical
state from the legacy scalars. The last test runs it against a real database and proves it is
read-only.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.engine_state_codec import payload_hash
from app.engine.state_bridge import default_engine_state_dict
from app.models.athlete_state import AthleteState
from app.models.user import User
from app.repositories.athlete_context_repository import AthleteContextRepository
from app.scripts import audit_engine_state as sweep

T1 = datetime(2026, 7, 1, 12, 0)
T2 = datetime(2026, 7, 2, 12, 0)
CANONICAL = default_engine_state_dict()
EMPTY = {"version": 2, "x": {}, "f": {}, "t": {}}
SECRET = {"version": 2, "x": {"aerobic": 1234.5678}, "note": "athlete-private-content"}


def _row(id_: int, user_id: int, ts: datetime, payload: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_, user_id=user_id, timestamp=ts, engine_state=payload,
        c_met_aerobic=50.0, c_nm_force=1200.0, c_struct=60.0, b_met_anaerobic=9000.0,
        f_met_systemic=10.0, f_nm_peripheral=12.0, f_nm_central=8.0, f_struct_damage=15.0,
    )


def test_heads_break_timestamp_ties_by_id_like_the_repository() -> None:
    """Two rows at one instant (a workout and its e1RM observations): the higher id is the
    head, exactly as `get_latest_state` orders — never both, never the earlier one."""
    rows = [_row(5, 1, T2, EMPTY), _row(9, 1, T2, CANONICAL), _row(3, 1, T1, CANONICAL)]
    assert sweep.select_heads(rows)[1].id == 9


def test_groups_partition_rows_and_heads_are_reported_apart() -> None:
    rows = [
        _row(1, 1, T1, None), _row(2, 1, T2, CANONICAL),             # healed: history only
        _row(3, 2, T1, EMPTY),                                       # incomplete head
        _row(4, 3, T1, CANONICAL | {"version": 99}),                 # future-version head
        _row(5, 4, T1, {"version": 2, "x": "nope", "f": {}, "t": {}}),  # malformed head
    ]
    report, _ = sweep.summarize(rows, users_total=6, active_user_ids={2}, deployed_sha="abc")

    assert report.heads == {"canonical": 1, "missing": 0, "incomplete": 1, "malformed": 1,
                            "unsupported_version": 1, "nonfinite": 0}
    assert report.historical == {"canonical": 0, "missing": 1, "incomplete": 0, "malformed": 0,
                                 "unsupported_version": 0, "nonfinite": 0}
    assert sum(report.heads.values()) + sum(report.historical.values()) == report.rows_total == 5
    # "No state row" is absence, not damage: 6 users, 4 with rows.
    assert report.users_without_state == 2
    assert report.noncanonical_heads == 3
    assert report.noncanonical_heads_active_90d == 1
    assert (report.deployed_sha, report.codec_version) == ("abc", "v2")


@pytest.mark.parametrize(
    ("rows", "code", "must", "must_not"),
    [
        pytest.param([_row(1, 1, T1, CANONICAL)], 0,
                     ["CLEAR FOR C1", "zero rows depend on the legacy columns", "recheck"],
                     ["BLOCKS", "HISTORY"], id="all-canonical"),
        pytest.param([_row(1, 1, T1, None), _row(2, 1, T2, CANONICAL)], 0,
                     ["CLEAR FOR C1", "Retain their degraded-display inputs"],
                     ["BLOCKS", "zero rows"], id="history-only"),
        pytest.param([_row(1, 1, T1, None)], 1,
                     ["BLOCKS C1 DEPLOY", "trustworthy canonical history", "Do NOT backfill"],
                     ["CLEAR"], id="damaged-head"),
        pytest.param([_row(1, 1, T1, CANONICAL | {"version": 99})], 1,
                     ["BLOCKS C1 DEPLOY", "deploy readers first"], ["CLEAR"], id="future-head"),
    ],
)
def test_the_verdict_follows_the_storage_rule(
    rows: list[Any], code: int, must: list[str], must_not: list[str]
) -> None:
    report, _ = sweep.summarize(rows, users_total=1, active_user_ids=set(), deployed_sha="x")
    got_code, lines = sweep.verdict_lines(report)
    text = "\n".join(lines)
    assert got_code == code
    for phrase in must:
        assert phrase in text, (phrase, text)
    for phrase in must_not:
        assert phrase not in text, (phrase, text)
    # Never a recommendation to reconstruct canonical state from the mirror.
    assert "Backfill through" not in text and "reconstruction, marked" not in text


def test_no_payload_content_reaches_the_report() -> None:
    report, verdicts = sweep.summarize(
        [_row(1, 1, T1, SECRET)], users_total=1, active_user_ids=set(), deployed_sha="x"
    )
    blob = json.dumps(report.as_dict()) + repr(verdicts)
    assert "athlete-private-content" not in blob and "1234.5678" not in blob



@pytest.mark.asyncio
async def test_the_sweep_is_read_only_and_agrees_with_the_repository(
    async_db: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    users = [User(email=f"sweep{i}@t.io", hashed_password="x", is_active=True) for i in range(3)]
    async_db.add_all(users)
    await async_db.flush()
    a, b, _no_state = users
    for uid, ts, payload in [
        (a.id, T1, None), (a.id, T2, CANONICAL), (a.id, T2, EMPTY), (b.id, T1, SECRET)
    ]:
        async_db.add(AthleteState(
            user_id=uid, timestamp=ts, engine_state=payload,
            c_met_aerobic=50.0, c_nm_force=1200.0, c_struct=60.0, b_met_anaerobic=9000.0,
            f_met_systemic=10.0, f_nm_peripheral=12.0, f_nm_central=8.0, f_struct_damage=15.0,
            s_struct_signal=0.0, habit_strength=0.5, skill_state={},
        ))
    await async_db.commit()

    def fingerprint(rows: list[AthleteState]) -> list[tuple[int, str]]:
        return sorted((r.id, payload_hash(r.engine_state)) for r in rows)

    before = fingerprint(list((await async_db.execute(select(AthleteState))).scalars().all()))

    code = await sweep.audit_with_db(async_db, deployed_sha="test-sha", as_json=True)
    out = json.loads(capsys.readouterr().out)

    # The sweep's head for athlete A is the repository's head (the tie at T2 goes to the
    # higher id, the EMPTY row), so the sweep and the strict loaders judge the same row.
    repo_head = await AthleteContextRepository(async_db).get_latest_state(a.id)
    assert repo_head is not None and repo_head.engine_state == EMPTY
    # A's head is EMPTY (vector_empty); B's is SECRET, which lacks f and t (missing_vectors).
    assert out["heads"] == {"canonical": 0, "missing": 0, "incomplete": 2, "malformed": 0,
                            "unsupported_version": 0, "nonfinite": 0}
    assert out["historical"] == {"canonical": 1, "missing": 1, "incomplete": 0,
                                 "malformed": 0, "unsupported_version": 0, "nonfinite": 0}
    assert out["users_total"] == 3 and out["users_without_state"] == 1
    assert (out["deployed_sha"], out["codec_version"], out["exit_code"], code) == (
        "test-sha", "v2", 1, 1
    )
    assert "athlete-private-content" not in json.dumps(out)
    # Read-only: every row and payload exactly as before.
    async_db.expire_all()
    after = fingerprint(list((await async_db.execute(select(AthleteState))).scalars().all()))
    assert after == before
