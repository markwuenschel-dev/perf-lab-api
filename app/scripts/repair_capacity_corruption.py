"""Conservative repair for ADR-0055 capacity corruption.

Pre-hotfix, workout-derived e1RM extraction could regress ``max_strength`` (a
submaximal set's low extrapolated e1RM pulled capacity down). Migration ``a025``
strips those observation rows of capacity authority and the runtime guard stops all
new damage — but athletes whose ``max_strength`` was already dragged down in the
append-only state series stay corrupted until repaired.

This job is **dry-run by default** and **conservative + monotonic**: for each affected
athlete (those with any ``workout_extraction`` observation), it restores ``max_strength``
to the athlete's own historical high-watermark by appending ONE correction state row —
it never lowers anything. Review the dry-run before ``--apply``.

    python -m app.scripts.repair_capacity_corruption            # dry run
    python -m app.scripts.repair_capacity_corruption --apply    # write corrections

Exit: 0 = every athlete processed · 1 = partial failure, some athletes refused.

Caveat: a genuine long-layoff detraining drop is also floored here; that is the
intended conservative direction (never silently leave capacity corrupted), and it only
touches athletes who have workout-extraction rows. Detraining is modelled elsewhere.

Why this job decodes strictly (INT-15 W1-A, slice S4)
----------------------------------------------------
This is a **canonical mutation path**: it appends an ``AthleteState`` row that becomes the
newest state, which every decision surface then reads. It is therefore strict, by the same
rule as ``benchmark_service`` — a caller is classified by what it can *do*, not by the word
"repair" in its name. It is NOT the INT-15 forensic engine_state repair utility; that path
(``read_raw_state_for_repair``) is specified but unimplemented, and this job is not it.

It previously read through ``state_bridge.unified_from_athlete_row``, which silently
reconstructs from the lossy legacy scalar mirror when ``engine_state`` is absent or
damaged. The consequence was not a failed repair but a laundered one: the reconstruction
was copied, restamped ``version: 2`` by ``athlete_state_kwargs_from_unified``, and
committed as the athlete's newest canonical state — provenance gone, indistinguishable
from an originally observed vector. Restoring a high-watermark is worthless if the vector
carrying it is an inference wearing canonical clothes.

Both reads are strict, and both matter:

1. **The mutation base.** The latest row must decode, or the athlete is refused — there is
   no honest state to copy the correction onto.
2. **The watermark sources.** *Every* historical row feeding the maximum must decode too.
   Skipping undecodable rows would quietly redefine "the athlete's historical high-water
   mark" as "the highest value among rows that still parse" — a different, weaker claim
   that happens to look identical in the output. If that is ever the right rule it must be
   an explicit ADR refinement, not a silent consequence of this fix.

Refusal is per-athlete and never partial: a refused athlete gets no writes at all. Only
identifiers and normalized reasons are printed — raw ``engine_state`` is athlete data.
"""
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.engine.engine_state_codec import (
    INCOMPLETE_ERROR_CODES,
    EngineStateDecodeError,
    MalformedCurrentEngineState,
    MissingEngineState,
    UnsupportedFutureEngineStateVersion,
    inspect_declared_version,
)
from app.engine.state_bridge import athlete_state_kwargs_from_unified
from app.engine.state_loading import unified_from_athlete_row_strict
from app.models.athlete_state import AthleteState
from app.models.benchmark_observation import BenchmarkObservation

_EPS = 0.5  # kg — ignore trivial floating drift


@dataclass(frozen=True)
class Refusal:
    """One athlete this job declined to touch, and why."""

    user_id: int
    latest_state_row_id: int | None
    reason: str
    declared_version: int | None
    detail: str = ""


@dataclass(frozen=True)
class RepairReport:
    corrected: int
    refused: list[Refusal]

    @property
    def partial_failure(self) -> bool:
        return bool(self.refused)

    @property
    def exit_code(self) -> int:
        return 1 if self.partial_failure else 0


def _latest_refusal_reason(exc: EngineStateDecodeError) -> str:
    """Name the outcome for a repair operator. `decode_engine_state` stays the only oracle
    of "valid" — this maps its failure to a reason, it does not re-decide validity.

    Future-version is deliberately distinct from malformed: that row is not damaged and
    must never be restamped; this reader is simply too old for it.
    """
    if isinstance(exc, MissingEngineState):
        return "latest_state_null"
    if isinstance(exc, UnsupportedFutureEngineStateVersion):
        return "latest_state_future_version"
    if isinstance(exc, MalformedCurrentEngineState) and exc.error_code in INCOMPLETE_ERROR_CODES:
        return "latest_state_incomplete"
    return "latest_state_malformed"


async def _affected_user_ids(db: AsyncSession) -> list[int]:
    res = await db.execute(
        select(BenchmarkObservation.user_id)
        .where(BenchmarkObservation.source == "workout_extraction")
        .distinct()
    )
    return [r[0] for r in res.all() if r[0] is not None]


async def repair_with_db(db: AsyncSession, apply: bool) -> RepairReport:
    """Core repair against a given session — what was corrected, and what was refused."""
    corrected = 0
    refused: list[Refusal] = []
    user_ids = await _affected_user_ids(db)
    print(f"[repair] {len(user_ids)} athlete(s) with workout_extraction evidence")

    for uid in user_ids:
        rows = (
            await db.execute(
                select(AthleteState)
                .where(AthleteState.user_id == uid)
                .order_by(AthleteState.timestamp.asc())
            )
        ).scalars().all()
        if not rows:
            continue

        latest_row = rows[-1]

        # 1. The mutation base must be canonical — we are about to copy it forward.
        try:
            latest = unified_from_athlete_row_strict(latest_row)
        except EngineStateDecodeError as exc:
            refused.append(
                Refusal(
                    user_id=uid,
                    latest_state_row_id=latest_row.id,
                    reason=_latest_refusal_reason(exc),
                    declared_version=inspect_declared_version(latest_row.engine_state),
                )
            )
            continue

        # 2. Every watermark source must be canonical too — see the module docstring.
        strengths: list[float] = []
        undecodable: AthleteState | None = None
        for r in rows:
            try:
                strengths.append(unified_from_athlete_row_strict(r).capacity_x.max_strength)
            except EngineStateDecodeError:
                undecodable = r
                break
        if undecodable is not None:
            refused.append(
                Refusal(
                    user_id=uid,
                    latest_state_row_id=latest_row.id,
                    reason="watermark_source_invalid",
                    declared_version=inspect_declared_version(undecodable.engine_state),
                    detail=f"state row {undecodable.id} does not decode",
                )
            )
            continue

        watermark = max(strengths)
        current = latest.capacity_x.max_strength
        if current >= watermark - _EPS:
            continue

        print(
            f"[repair] user {uid}: max_strength {current:.1f} < watermark "
            f"{watermark:.1f} → restore (+{watermark - current:.1f} kg)"
        )
        corrected += 1
        if apply:
            fixed = latest.model_copy(deep=True)
            fixed.capacity_x.max_strength = watermark
            fixed.timestamp = datetime.now(UTC).replace(tzinfo=None)
            kwargs = athlete_state_kwargs_from_unified(fixed)
            row = AthleteState(user_id=uid, **kwargs)
            row.engine_state = {
                **(row.engine_state or {}),
                "correction": {
                    "reason": "adr0055_capacity_corruption_repair",
                    "restored_max_strength": round(watermark, 2),
                    "from": round(current, 2),
                },
            }
            db.add(row)

    if apply and corrected:
        await db.commit()

    _print_refusals(refused)
    verb = "applied" if apply else "would correct (dry run)"
    tail = "Done." if apply else "Run with --apply to write."
    print(f"[repair] {verb} {corrected} athlete(s). {tail}")
    return RepairReport(corrected=corrected, refused=refused)


def _print_refusals(refused: list[Refusal]) -> None:
    """Refusals are the headline, not a footnote — a silent skip reads as "nothing to do"."""
    if not refused:
        return
    print(f"\n[repair] REFUSED {len(refused)} athlete(s) — state does not decode strictly.")
    print("[repair] No rows were written for these athletes.")
    for r in sorted(refused, key=lambda x: x.user_id):
        version = f"v{r.declared_version}" if r.declared_version is not None else "no version"
        detail = f" — {r.detail}" if r.detail else ""
        print(
            f"[repair]   user {r.user_id}: {r.reason} "
            f"(latest state row {r.latest_state_row_id}, {version}){detail}"
        )
    print(
        "[repair] These need inspection before repair. Capacity repair cannot proceed from "
        "state it cannot read — reconstructing it would write an inference as canonical."
    )


async def repair(apply: bool) -> RepairReport:
    async with AsyncSessionLocal() as db:
        return await repair_with_db(db, apply)


if __name__ == "__main__":
    report = asyncio.run(repair(apply="--apply" in sys.argv))
    sys.exit(report.exit_code)
