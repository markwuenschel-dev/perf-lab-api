"""One per-athlete lock for the canonical ``athlete_states`` chain (F2).

Every writer of an ``AthleteState`` row reads the athlete's latest state, evolves it, and
appends the result. Two writers that read the same predecessor both append from it, and
one update is lost — a double-submitted workout, a workout racing a benchmark, a workout
racing the first-time baseline. This lock serializes them.

The protocol (enforced by ``tests/test_state_chain_lock.py``):

* Every function that inserts a state row — or stages a baseline — calls
  :func:`lock_athlete_chain` BEFORE it reads the predecessor, in the same transaction that
  inserts and commits the row.
* The lock is transaction-scoped and ends at commit or rollback. A writer that commits and
  then writes again (the post-workout e1RM observations) takes it again and re-reads.
* It is re-entrant within a transaction, so a writer composed of locked helpers is fine.
* **Lock order: this lock first.** Onboarding also locks the user row
  (``SELECT … FOR UPDATE``), and every state insert takes a ``KEY SHARE`` lock on that same
  row through its foreign key — which ``FOR UPDATE`` blocks. Taking the user row first and
  this lock second would deadlock against a workout that holds this lock and is inserting.

Distinct from the shadow EKF chain lock (``ekf_shadow_service``), which guards a different
chain in a different (telemetry) transaction.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: "STA1" — the namespace half of the two-int advisory lock; within signed int4. users.id is a
#: 32-bit Integer PK, so the (namespace, user_id) form is exact.
STATE_CHAIN_LOCK_NAMESPACE = 0x53544131


async def lock_athlete_chain(db: AsyncSession, user_id: int) -> None:
    """Block until this transaction holds the athlete's state-chain lock (F2).

    Call before reading the predecessor state, in the transaction that will insert the next
    row. Released automatically at commit/rollback.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :uid)"),
        {"ns": STATE_CHAIN_LOCK_NAMESPACE, "uid": user_id},
    )
