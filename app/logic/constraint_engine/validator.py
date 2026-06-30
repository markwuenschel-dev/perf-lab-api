"""Run structured template constraints against a candidate session dict."""

from __future__ import annotations

import logging
from typing import Any

from app.logic.constraint_engine.constraints_impl import (
    CONSTRAINT_REGISTRY,
    UNIVERSAL_HARD_CONSTRAINTS,
    UNIVERSAL_SOFT_CONSTRAINTS,
)
from app.logic.constraint_engine.types import ConstraintContext, ValidationReport
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
            report.skipped_codes.append(code)
            logger.warning("constraint code not registered: %s", code)
            return
        try:
            result = fn(candidate, ctx)
        except Exception:
            logger.exception("constraint %s crashed; skipping", code)
            report.skipped_codes.append(code)
            return

        if result.passed:
            return

        msg = (result.message or "").strip() or code
        if is_hard:
            report.hard_failed.append(msg)
        else:
            report.soft_warnings.append(msg)
