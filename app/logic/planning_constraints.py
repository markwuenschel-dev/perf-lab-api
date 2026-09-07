"""The solver's typed constraint input — ADR-0064's first seam.

A `ResolvedPlanningConstraint` is one rule the planner must obey, carried as data
rather than as a function, a string code, or an entry in an untyped dict. ADR-0064:89
requires exactly that: *"the solver's constraint input is a typed, scoped,
provenance-bearing record ... **never** an untyped dict."*

Why this exists rather than reusing what is already here:

- `constraint_engine.ConstraintResult` is a typed *outcome*, not a typed *rule*. The
  rule behind it is a bare string code in `CONSTRAINT_REGISTRY: dict[str, Any]`, and its
  hardness is decided by which of two module-level *lists* the string appears in — not by
  the `Severity` on the result, which `SessionValidator` discards into a list of message
  strings.
- Those rules also run in the wrong place for exclusion. They validate the winner
  *after* selection (`prescription_finalize`), and their only effect is to replace the
  whole prescription with a hardcoded Recovery session. A constraint that should have
  removed one candidate from a pool of many cannot express itself that way.

So this module owns *whether a candidate is eligible*, and deliberately owns nothing
else. It does not read `PlanningOverride` — ADR-0064:95 is explicit that the scheduling
core never does; P12 compiles those rows into constraints of this type and hands them in.
That is what makes P12 an insertion rather than a rewrite, and it is why `authority_class`
already carries a `user_override` member that nothing in P11 produces.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.logic.constraint_engine.candidate import SessionCandidate

# Bumped when the *meaning* of a constraint kind or of exclusion changes, so a trace
# recorded today can still be read correctly later. Not a package version.
CONSTRAINT_POLICY_VERSION = "pc-v1"


class Hardness(str, Enum):
    """Whether a constraint may be traded away.

    Distinct from `constraint_engine.Severity` on purpose: that type answers "did this
    rule pass for this session", which is a validation outcome. This answers "may the
    optimizer disregard this", which is an authority question.
    """

    HARD = "hard"
    SOFT = "soft"


class AuthorityClass(str, Enum):
    """Where a constraint's authority comes from (ADR-0064:90-92).

    Ordered most- to least-authoritative, matching the executable precedence at
    ADR-0064:77-78 — `safety > committed session revision > fixed schedule >
    objective mix/floors`. `USER_OVERRIDE` sits between commitment and schedule per
    ADR-0051's stack, and is produced only by P12; P11 never emits one.
    """

    SAFETY = "safety"
    COMMITMENT = "commitment"
    USER_OVERRIDE = "user_override"
    SCHEDULE = "schedule"
    OBJECTIVE_POLICY = "objective_policy"


class ConstraintScope(str, Enum):
    """What a constraint binds (ADR-0064:91)."""

    SESSION_REVISION = "session_revision"
    SLOT = "slot"
    MICROCYCLE = "microcycle"
    BLOCK = "block"
    PHASE = "phase"


class ConstraintKind(str, Enum):
    """The predicate vocabulary. Closed on purpose.

    A new kind is a deliberate edit here plus a branch in `_excludes`, so an unrecognised
    kind can never be silently ignored — the alternative (free-form strings) is how a
    stored constraint ends up matching nothing while the UI reports it as applied.

    Only kinds decidable at *candidate* level are listed. A `SessionCandidate` carries
    `exercise_slots` — declarative requirements — not resolved movements, so a
    movement-level exclusion ("never prescribe back squat") genuinely cannot be answered
    here. It belongs at slot resolution, which receives the same constraint set, and is
    deliberately deferred rather than approximated by matching a slot's `e1rm_code` and
    calling it a name.

    `EXCLUDE_MODALITY` is what P12 will compile an `exclude_modality` override into, and
    is the kind the closure test (ADR-0064:107) injects.
    """

    EXCLUDE_MODALITY = "exclude_modality"
    EXCLUDE_DOMAIN = "exclude_domain"
    EXCLUDE_SESSION_TYPE = "exclude_session_type"
    MAX_DURATION_MIN = "max_duration_min"


@dataclass(frozen=True)
class ResolvedPlanningConstraint:
    """One rule the planner must obey, with the provenance to explain itself.

    Frozen because a constraint is an input to a decision: if the solver could mutate one
    mid-run, the trace it emits would not describe the run that happened.

    `target` is a plain string rather than a dict so that equality, hashing, and logging
    are all trivial and a malformed target cannot hide inside nested JSON. Kinds that
    need a number carry it in `threshold`.
    """

    kind: ConstraintKind
    hardness: Hardness
    authority_class: AuthorityClass
    scope: ConstraintScope
    reason_code: str
    source_type: str
    target: str = ""
    threshold: float | None = None
    source_id: int | None = None
    actor_id: int | None = None
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    policy_version: str = CONSTRAINT_POLICY_VERSION

    def applies_at(self, when: datetime) -> bool:
        """Whether this constraint is in force at `when`.

        Both bounds are optional and open-ended; a constraint with neither always applies.
        """
        if self.effective_from is not None and when < self.effective_from:
            return False
        if self.effective_until is not None and when > self.effective_until:
            return False
        return True


@dataclass(frozen=True)
class ConstraintExclusion:
    """Why one candidate was removed. The unit the decision trace is built from."""

    candidate_type: str
    branch_id: str
    constraint: ResolvedPlanningConstraint

    @property
    def reason(self) -> str:
        c = self.constraint
        target = f"={c.target}" if c.target else ""
        return f"{c.kind.value}{target}:{c.reason_code}"


@dataclass
class ConstraintApplication:
    """The result of applying a constraint set to a candidate pool.

    Carries the survivors *and* what was removed, because the two are equally load-bearing:
    an empty `survivors` with an empty `exclusions` is a generator failure, while an empty
    `survivors` with exclusions is an infeasible request, and the caller must be able to
    tell those apart.
    """

    survivors: list[SessionCandidate] = field(default_factory=lambda: [])
    exclusions: list[ConstraintExclusion] = field(default_factory=lambda: [])
    soft_hits: list[ConstraintExclusion] = field(default_factory=lambda: [])

    @property
    def infeasible(self) -> bool:
        """No candidate survived, and a hard constraint is the reason."""
        return not self.survivors and bool(self.exclusions)

    def reason_codes(self) -> list[str]:
        """Stable, de-duplicated reasons, for the explanation channel."""
        return list(dict.fromkeys(e.reason for e in self.exclusions))


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _excludes(constraint: ResolvedPlanningConstraint, candidate: SessionCandidate) -> bool:
    """Whether this constraint removes this candidate.

    Every kind is handled explicitly and the function returns False for anything it does
    not recognise *only* because the enum is closed — an unknown kind cannot be
    constructed. Kept pure and free of DB access so the seam is unit-testable without a
    database and deterministic under replay.
    """
    target = _norm(constraint.target)

    if constraint.kind is ConstraintKind.EXCLUDE_DOMAIN:
        return _norm(candidate.domain) == target

    if constraint.kind is ConstraintKind.EXCLUDE_SESSION_TYPE:
        return target in _norm(candidate.type)

    if constraint.kind is ConstraintKind.EXCLUDE_MODALITY:
        # A candidate's modality is not a field on SessionCandidate; the closest honest
        # proxies are its domain and the slots it would fill. Both are checked so an
        # exclusion cannot be defeated by the modality only being visible on the slots.
        if _norm(candidate.domain) == target:
            return True
        return any(_norm(slot.modality) == target for slot in candidate.exercise_slots)

    if constraint.kind is ConstraintKind.MAX_DURATION_MIN:
        if constraint.threshold is None:
            return False
        return candidate.duration_min > constraint.threshold

    return False


def apply_constraints(
    candidates: Sequence[SessionCandidate],
    constraints: Iterable[ResolvedPlanningConstraint],
    *,
    now: datetime | None = None,
) -> ConstraintApplication:
    """Remove candidates barred by HARD constraints; record SOFT hits without removing.

    This is the whole seam. It sits between pool assembly and scoring precisely because
    that is the last point at which the pool is a fully-typed `list[SessionCandidate]`
    and nothing has yet collapsed a candidate into a score.

    A SOFT constraint never removes. ADR-0064 is explicit that a soft preference is
    tradeable by the optimizer, and there is no scored alternative or tradeoff trace yet
    to trade it against — so recording the hit and leaving selection alone is the only
    honest thing this layer can do. Silently treating soft as hard would be the exact
    failure the earlier design review named.
    """
    active = [c for c in constraints if now is None or c.applies_at(now)]
    hard = [c for c in active if c.hardness is Hardness.HARD]
    soft = [c for c in active if c.hardness is Hardness.SOFT]

    out = ConstraintApplication()
    for candidate in candidates:
        blocking = next((c for c in hard if _excludes(c, candidate)), None)
        if blocking is not None:
            out.exclusions.append(
                ConstraintExclusion(candidate.type, candidate.branch_id, blocking)
            )
            continue
        for c in soft:
            if _excludes(c, candidate):
                out.soft_hits.append(
                    ConstraintExclusion(candidate.type, candidate.branch_id, c)
                )
        out.survivors.append(candidate)
    return out
