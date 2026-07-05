"""Shared base for the offline validation-gate reports.

Holds only the fields common to the regression/classifier gate reports
(q1_decrement, q2_recovery, q3_tissue, q6_deload): the held-out size, the decile
calibration error, and the verdict + reasons. Each pipeline's ``EvalReport`` subclasses
this and adds its own metric fields (regression: mae_*/sign_accuracy/saturation_fraction;
classifier: auc_*/brier_*/positive_rate). ``as_dict`` returns ``asdict(self)`` over the
combined field set.

All fields carry defaults so subclasses may append their own fields (dataclass inheritance
forbids a non-default field after a defaulted one). Every call site constructs by keyword
with all fields supplied, so the defaults are never materialized — behavior is unchanged.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BaseEvalReport:
    n_test_rows: int = 0
    n_test_athletes: int = 0
    calibration_error: float = 0.0
    verdict: str = ""            # "promote" | "stay_shadow"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
