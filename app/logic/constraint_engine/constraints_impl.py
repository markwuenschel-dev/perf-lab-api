"""Registered constraint callables for bundled templates A–E (best-effort + degraded mode)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.logic.constraint_engine.types import ConstraintContext, ConstraintResult, Severity

logger = logging.getLogger(__name__)


def _ok(
    code: str,
    *,
    hard: bool = True,
    insufficient: bool = False,
    msg: str = "",
) -> ConstraintResult:
    sev = Severity.HARD if hard else Severity.SOFT
    return ConstraintResult(True, sev, code, msg, insufficient_history=insufficient)


def _fail(
    code: str,
    *,
    hard: bool = True,
    msg: str = "",
) -> ConstraintResult:
    sev = Severity.HARD if hard else Severity.SOFT
    return ConstraintResult(False, sev, code, msg, insufficient_history=False)


def _parse_ts(row: dict[str, Any]) -> datetime | None:
    ts = row.get("session_timestamp")
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts
    if isinstance(ts, str):
        try:
            s = ts.replace("Z", "+00:00")
            return datetime.fromisoformat(s)
        except ValueError:
            return None
    return None


def _within_hours(ts: datetime | None, hours: float) -> bool:
    if ts is None:
        return False
    now = datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (now - ts) <= timedelta(hours=hours)


def _sessions_in_window(
    sessions: list[dict[str, Any]], days: int
) -> list[dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    out: list[dict[str, Any]] = []
    for s in sessions:
        ts = _parse_ts(s)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts >= cutoff:
            out.append(s)
    return out


# --- Olympic ---


def olift_limit_reps_per_set_competition_lifts(
    c: dict[str, Any], _ctx: ConstraintContext
) -> ConstraintResult:
    cap = c.get("max_reps_per_set")
    if cap is None:
        return _ok("olift_limit_reps_per_set_competition_lifts", insufficient=True)
    if cap > 5:
        return _fail(
            "olift_limit_reps_per_set_competition_lifts",
            msg="Competition lift reps per set should stay ≤5",
        )
    return _ok("olift_limit_reps_per_set_competition_lifts")


def olift_no_high_fatigue_metcon_before_technical_session(
    c: dict[str, Any], ctx: ConstraintContext
) -> ConstraintResult:
    if not ctx.recent_sessions:
        return _ok(
            "olift_no_high_fatigue_metcon_before_technical_session",
            insufficient=True,
            msg="insufficient_history",
        )
    tags = set(c.get("tags") or [])
    if "technical_olift" not in tags:
        return _ok("olift_no_high_fatigue_metcon_before_technical_session")

    last = ctx.recent_sessions[0]
    last_tags = set(last.get("tags") or [])
    rpe = float(last.get("session_rpe") or 0)
    if "metcon_high_density" in last_tags or (
        "metcon" in last_tags and rpe >= 7.5 and float(last.get("duration_minutes") or 0) >= 25
    ):
        ts = _parse_ts(last)
        if ts and _within_hours(ts, 48):
            return _fail(
                "olift_no_high_fatigue_metcon_before_technical_session",
                msg="Dense metcon too close to technical classical work",
            )
    return _ok("olift_no_high_fatigue_metcon_before_technical_session")


def olift_require_freshness_for_heavy_technical_singles(
    c: dict[str, Any], ctx: ConstraintContext
) -> ConstraintResult:
    tags = set(c.get("tags") or [])
    if "heavy_technical" not in tags and c.get("intensity_bucket") not in ("high", "max"):
        return _ok("olift_require_freshness_for_heavy_technical_singles")
    cns = float(ctx.legacy.get("f_nm_central", 0))
    if cns > 58:
        return _fail(
            "olift_require_freshness_for_heavy_technical_singles",
            msg="CNS fatigue too high for heavy technical singles",
        )
    return _ok("olift_require_freshness_for_heavy_technical_singles")


def prefer_competition_lifts_2_to_4_exposures_week(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_competition_lifts_2_to_4_exposures_week", hard=False)


def prefer_squat_2_to_3_times_week(c: dict[str, Any], ctx: ConstraintContext) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_squat_2_to_3_times_week", hard=False)


def prefer_pulls_after_classical_lifts(c: dict[str, Any], ctx: ConstraintContext) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_pulls_after_classical_lifts", hard=False)


# --- Running ---


def run_limit_high_intensity_sessions_per_week(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    recent = _sessions_in_window(ctx.recent_sessions, 7)
    if len(recent) < 2:
        return _ok("run_limit_high_intensity_sessions_per_week", insufficient=True)
    hi = 0
    for s in recent:
        tags = set(s.get("tags") or [])
        if "threshold_or_vo2" in tags or "high_intensity_run" in tags:
            hi += 1
    if hi >= 3 and c.get("intensity_bucket") in ("high", "max"):
        return _fail(
            "run_limit_high_intensity_sessions_per_week",
            msg="High-intensity running exposures capped for the week",
        )
    return _ok("run_limit_high_intensity_sessions_per_week")


def run_no_back_to_back_threshold_and_vo2_days(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    if len(ctx.recent_sessions) < 2:
        return _ok("run_no_back_to_back_threshold_and_vo2_days", insufficient=True)
    if c.get("intensity_bucket") not in ("high", "max") and "threshold_or_vo2" not in (
        c.get("tags") or []
    ):
        return _ok("run_no_back_to_back_threshold_and_vo2_days")

    s0 = _parse_ts(ctx.recent_sessions[0])
    s1 = _parse_ts(ctx.recent_sessions[1])
    if s0 and s1:
        d0, d1 = s0.date(), s1.date()
        if abs((d0 - d1).days) == 1:
            t0 = set(ctx.recent_sessions[0].get("tags") or [])
            t1 = set(ctx.recent_sessions[1].get("tags") or [])
            if "threshold_or_vo2" in t0 and "threshold_or_vo2" in t1:
                return _fail(
                    "run_no_back_to_back_threshold_and_vo2_days",
                    msg="Avoid back-to-back threshold/VO2 quality days",
                )
    return _ok("run_no_back_to_back_threshold_and_vo2_days")


def run_limit_long_run_ramp_rate(c: dict[str, Any], ctx: ConstraintContext) -> ConstraintResult:
    if "long_run" not in (c.get("tags") or []):
        return _ok("run_limit_long_run_ramp_rate")
    long_durs = [
        float(s.get("duration_minutes") or 0)
        for s in ctx.recent_sessions
        if "long_easy" in (s.get("tags") or []) or "long_run" in (s.get("tags") or [])
    ]
    if not long_durs:
        return _ok("run_limit_long_run_ramp_rate", insufficient=True)
    prev = max(long_durs)
    proposed = float(c.get("duration_min") or 0)
    if prev > 0 and proposed > prev * 1.25:
        return _fail(
            "run_limit_long_run_ramp_rate",
            msg="Long-run duration ramp too aggressive vs recent history",
        )
    return _ok("run_limit_long_run_ramp_rate")


def prefer_majority_easy_volume(c: dict[str, Any], ctx: ConstraintContext) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_majority_easy_volume", hard=False)


def prefer_threshold_before_vo2_if_base_low(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    tags = list(c.get("tags") or [])
    if ctx.athlete_state.get("c_met_aerobic", 50) < 25 and (
        "vo2_candidate" in tags or "threshold_or_vo2" in tags
    ):
        return _fail(
            "prefer_threshold_before_vo2_if_base_low",
            hard=False,
            msg="Aerobic base low — bias threshold before VO2",
        )
    return _ok("prefer_threshold_before_vo2_if_base_low", hard=False)


def prefer_strides_after_easy_days(c: dict[str, Any], ctx: ConstraintContext) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_strides_after_easy_days", hard=False)


# --- Powerlifting 531 ---


def pl_cap_deadlift_heavy_exposures(c: dict[str, Any], ctx: ConstraintContext) -> ConstraintResult:
    recent = _sessions_in_window(ctx.recent_sessions, 7)
    if len(recent) < 1:
        return _ok("pl_cap_deadlift_heavy_exposures", insufficient=True)
    dl = 0
    for s in recent:
        st = str(s.get("modality", "")).lower()
        tg = set(s.get("tags") or [])
        if "deadlift" in st or "deadlift_session" in tg:
            dl += 1
    if dl >= 2 and "deadlift" in (c.get("main_lift") or ""):
        return _fail(
            "pl_cap_deadlift_heavy_exposures",
            msg="Deadlift heavy exposures limited for the week",
        )
    return _ok("pl_cap_deadlift_heavy_exposures")


def pl_do_not_max_when_readiness_low(c: dict[str, Any], ctx: ConstraintContext) -> ConstraintResult:
    if c.get("intensity_bucket") not in ("high", "max"):
        return _ok("pl_do_not_max_when_readiness_low")
    r = (
        float(ctx.legacy.get("f_nm_central", 0)) + float(ctx.legacy.get("f_met_systemic", 0))
    ) / 2
    if r > 62:
        return _fail(
            "pl_do_not_max_when_readiness_low",
            msg="Readiness low for max/competition intensity",
        )
    return _ok("pl_do_not_max_when_readiness_low")


def pl_limit_assistance_if_main_lift_fatigue_high(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    musc = float(ctx.fatigue_state.get("muscular", 0))
    if musc > 65 and float(c.get("volume_load_proxy") or 0) > 0.75:
        return _fail(
            "pl_limit_assistance_if_main_lift_fatigue_high",
            hard=False,
            msg="Reduce assistance volume when main-lift fatigue is high",
        )
    return _ok("pl_limit_assistance_if_main_lift_fatigue_high", hard=False)


def prefer_submax_training_max(c: dict[str, Any], ctx: ConstraintContext) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_submax_training_max", hard=False)


def prefer_slow_progression(c: dict[str, Any], ctx: ConstraintContext) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_slow_progression", hard=False)


def prefer_balanced_push_pull_accessory(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_balanced_push_pull_accessory", hard=False)


# --- Juggernaut ---


def pl_limit_volume_if_bar_speed_collapse(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    periph = float(ctx.legacy.get("f_nm_peripheral", 0))
    if periph > 60 and float(c.get("volume_load_proxy") or 0) > 0.72:
        return _fail(
            "pl_limit_volume_if_bar_speed_collapse",
            hard=False,
            msg="Peripheral fatigue high — limit volume",
        )
    return _ok("pl_limit_volume_if_bar_speed_collapse", hard=False)


def pl_limit_deadlift_volume_if_lumbar_fatigue_high(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    lum = float(ctx.tissue_state.get("lumbar", 0))
    if lum > 52 and c.get("main_lift") == "deadlift":
        return _fail(
            "pl_limit_deadlift_volume_if_lumbar_fatigue_high",
            msg="Lumbar stress high — limit deadlift volume",
        )
    return _ok("pl_limit_deadlift_volume_if_lumbar_fatigue_high")


def prefer_high_rep_accumulation_early_block(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_high_rep_accumulation_early_block", hard=False)


def prefer_intensification_late_block(c: dict[str, Any], ctx: ConstraintContext) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_intensification_late_block", hard=False)


def prefer_amrap_as_feedback_not_maxing(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_amrap_as_feedback_not_maxing", hard=False)


# --- Gymnastics ---


def gym_no_progression_without_prerequisites(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    if not ctx.skill_state:
        return _ok("gym_no_progression_without_prerequisites", insufficient=True)
    return _ok("gym_no_progression_without_prerequisites", hard=False)


def gym_limit_high_tendon_load_on_consecutive_days(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    ten = float(ctx.fatigue_state.get("tendon", 0))
    if ten < 48:
        return _ok("gym_limit_high_tendon_load_on_consecutive_days")
    if not ctx.recent_sessions:
        return _ok("gym_limit_high_tendon_load_on_consecutive_days", insufficient=True)
    last = ctx.recent_sessions[0]
    if "tendon_heavy" in (last.get("tags") or []):
        ts = _parse_ts(last)
        if ts and _within_hours(ts, 30):
            return _fail(
                "gym_limit_high_tendon_load_on_consecutive_days",
                msg="Tendon load stacked — insert recovery",
            )
    return _ok("gym_limit_high_tendon_load_on_consecutive_days")


def gym_limit_kipping_if_strict_strength_missing(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    if "kipping_skill" not in (c.get("tags") or []):
        return _ok("gym_limit_kipping_if_strict_strength_missing", hard=False)
    su = float(ctx.skill_state.get("pullup", ctx.skill_state.get("strict_pullup", 0.5)))
    if su < 0.42:
        return _fail(
            "gym_limit_kipping_if_strict_strength_missing",
            hard=False,
            msg="Build strict pulling strength before kipping volume",
        )
    return _ok("gym_limit_kipping_if_strict_strength_missing", hard=False)


def prefer_quality_singles_or_short_sets_for_skills(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_quality_singles_or_short_sets_for_skills", hard=False)


def prefer_isometric_progression_before_dynamic_complexity(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_isometric_progression_before_dynamic_complexity", hard=False)


def prefer_scapular_control_before_advanced_ring_work(
    c: dict[str, Any], ctx: ConstraintContext,
) -> ConstraintResult:
    _ = (c, ctx)
    return _ok("prefer_scapular_control_before_advanced_ring_work", hard=False)


CONSTRAINT_REGISTRY: dict[str, Any] = {
    "olift_limit_reps_per_set_competition_lifts": olift_limit_reps_per_set_competition_lifts,
    "olift_no_high_fatigue_metcon_before_technical_session": olift_no_high_fatigue_metcon_before_technical_session,
    "olift_require_freshness_for_heavy_technical_singles": olift_require_freshness_for_heavy_technical_singles,
    "prefer_competition_lifts_2_to_4_exposures_week": prefer_competition_lifts_2_to_4_exposures_week,
    "prefer_squat_2_to_3_times_week": prefer_squat_2_to_3_times_week,
    "prefer_pulls_after_classical_lifts": prefer_pulls_after_classical_lifts,
    "run_limit_high_intensity_sessions_per_week": run_limit_high_intensity_sessions_per_week,
    "run_no_back_to_back_threshold_and_vo2_days": run_no_back_to_back_threshold_and_vo2_days,
    "run_limit_long_run_ramp_rate": run_limit_long_run_ramp_rate,
    "prefer_majority_easy_volume": prefer_majority_easy_volume,
    "prefer_threshold_before_vo2_if_base_low": prefer_threshold_before_vo2_if_base_low,
    "prefer_strides_after_easy_days": prefer_strides_after_easy_days,
    "pl_cap_deadlift_heavy_exposures": pl_cap_deadlift_heavy_exposures,
    "pl_do_not_max_when_readiness_low": pl_do_not_max_when_readiness_low,
    "pl_limit_assistance_if_main_lift_fatigue_high": pl_limit_assistance_if_main_lift_fatigue_high,
    "prefer_submax_training_max": prefer_submax_training_max,
    "prefer_slow_progression": prefer_slow_progression,
    "prefer_balanced_push_pull_accessory": prefer_balanced_push_pull_accessory,
    "pl_limit_volume_if_bar_speed_collapse": pl_limit_volume_if_bar_speed_collapse,
    "pl_limit_deadlift_volume_if_lumbar_fatigue_high": pl_limit_deadlift_volume_if_lumbar_fatigue_high,
    "prefer_high_rep_accumulation_early_block": prefer_high_rep_accumulation_early_block,
    "prefer_intensification_late_block": prefer_intensification_late_block,
    "prefer_amrap_as_feedback_not_maxing": prefer_amrap_as_feedback_not_maxing,
    "gym_no_progression_without_prerequisites": gym_no_progression_without_prerequisites,
    "gym_limit_high_tendon_load_on_consecutive_days": gym_limit_high_tendon_load_on_consecutive_days,
    "gym_limit_kipping_if_strict_strength_missing": gym_limit_kipping_if_strict_strength_missing,
    "prefer_quality_singles_or_short_sets_for_skills": prefer_quality_singles_or_short_sets_for_skills,
    "prefer_isometric_progression_before_dynamic_complexity": prefer_isometric_progression_before_dynamic_complexity,
    "prefer_scapular_control_before_advanced_ring_work": prefer_scapular_control_before_advanced_ring_work,
}
