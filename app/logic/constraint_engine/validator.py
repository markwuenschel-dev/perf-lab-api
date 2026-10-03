"""Run structured template constraints against a candidate session dict."""

from __future__ import annotations

import logging
from typing import Any

from app.logic.constraint_engine.constraints_impl import (
    CONSTRAINT_REGISTRY,
    UNIVERSAL_HARD_CONSTRAINTS,
    UNIVERSAL_SOFT_CONSTRAINTS,
)
from app.logic.constraint_engine.types import (
    ConstraintContext,
    ConstraintResult,
    ValidationReport,
)
from app.schemas.coaching_template import StructuredCoachingTemplate

logger = logging.getLogger(__name__)


class SessionValidator:
    """Template-bound validator: hard codes block; soft codes add warnings."""

    def __init__(self, template: StructuredCoachingTemplate):
        self.template = template

    def validate(
        self,
        candidate: dict[str, Any],
        ctx: ConstraintContext,
    ) -> ValidationReport:
        report = ValidationReport()

        # Universal rules applied to every session regardless of template
        for code in UNIVERSAL_HARD_CONSTRAINTS:
            self._run_code(code, candidate, ctx, report, is_hard=True)
        for code in UNIVERSAL_SOFT_CONSTRAINTS:
            self._run_code(code, candidate, ctx, report, is_hard=False)

        # Template-specific rules
        for code in self.template.hard_constraints:
            self._run_code(code, candidate, ctx, report, is_hard=True)
        for code in self.template.soft_constraints:
            self._run_code(code, candidate, ctx, report, is_hard=False)

        return report

    def _run_code(
        self,
        code: str,
        candidate: dict[str, Any],
        ctx: ConstraintContext,
        report: ValidationReport,
        *,
        is_hard: bool,
    ) -> None:
        fn = CONSTRAINT_REGISTRY.get(code)
        if fn is None:
            if is_hard:
                # A hard safety rule that cannot run has not passed (W1-c). It used to land in
                # `skipped_codes` and count as a pass, sending the full session.
                report.unevaluated_hard.append(code)
                logger.error("hard constraint not registered, session not validated: %s", code)
            else:
                report.skipped_codes.append(code)
                logger.warning("constraint code not registered: %s", code)
            return
        try:
            result = fn(candidate, ctx)
        except Exception:
            if is_hard:
                report.unevaluated_hard.append(code)
                logger.exception("hard constraint %s crashed, session not validated", code)
            else:
                logger.exception("constraint %s crashed; skipping", code)
                report.skipped_codes.append(code)
            return
        # A rule that returned but did not produce a well-formed verdict has not evaluated
        # anything either: `None` (AttributeError on `.passed`) and a truthy non-bool `passed`
        # (which `if result.passed` would wave through) are the same failure as a crash.
        if not isinstance(result, ConstraintResult) or not isinstance(result.passed, bool):
            if is_hard:
                report.unevaluated_hard.append(code)
                logger.error(
                    "hard constraint %s returned a malformed result (%r), session not validated",
                    code,
                    result,
                )
            else:
                report.skipped_codes.append(code)
                logger.warning("constraint %s returned a malformed result (%r); skipping", code, result)
            return

        if result.passed:
            return

        msg = (result.message or "").strip() or code
        if is_hard:
            report.hard_failed.append(msg)
        else:
            report.soft_warnings.append(msg)
