"""Build the Q9 interference training frame (concurrent load -> adaptation efficiency).

Turns a stream of per-(athlete, adaptation-episode) benchmark blocks into a supervised
frame for learning the ADR-0037 CROSS-AXIS interference suppression curve

    gain_efficiency = floor + (1 - floor) * exp(-alpha * z)

where ``z`` is the concurrent INTERFERING load fraction that preceded the block (endurance
load for strength/power/hypertrophy; CNS fatigue for power/skill; structural for aerobic
quality) and ``gain_efficiency`` is the realized adaptation as a fraction of the athlete's
own zero-interference expectation. Does higher concurrent endurance load predict a SMALLER
strength gain? — that is exactly the signal this frame exposes.

The label is a per-athlete RATIO, never a raw post-block benchmark and never a value
derived from the same-block outcome that is being predicted. The zero-interference
expectation ``expected_gain`` is estimated ONLY from that athlete's own LOW-interference
anchor blocks (z ~= 0), so a whole-athlete holdout keeps it from leaking across the split.

The production-equivalent DB path is ``app.analysis.feature_builders.interference_features``
(``benchmark_observations`` joined to ``benchmark_definitions`` for ``domain`` /
``better_direction``); this pipeline plants a synthetic fixture with a KNOWN suppression
alpha so it runs and is tested without Postgres, and so the fit can be checked for alpha
recovery. See ``model_card``.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# The interfering-load fraction is the single pre-outcome predictor; the label is the
# realized-vs-expected adaptation ratio.
FEATURE_COLUMNS: tuple[str, ...] = ("z_interfering_load",)
LABEL_COLUMN = "gain_efficiency"
GROUP_COLUMN = "athlete_id"
PAIR_COLUMN = "interference_pair"
ORDER_COLUMN = "episode"

# Blocks with interfering-load fraction <= this are the athlete's zero-interference anchors,
# used to estimate the per-athlete expected (uninterfered) gain that normalizes the label.
LOW_Z_ANCHOR = 0.10
MIN_ANCHORS = 2
# Clip the efficiency ratio: a realized gain can slightly exceed / fall below the anchor
# expectation from noise, but a wildly out-of-range ratio is a degenerate normalizer.
_EFF_CLIP_LO = 0.01
_EFF_CLIP_HI = 1.60

# Features that are FORBIDDEN because they leak the label or are measured post-outcome.
# The label is realized_gain / expected_gain; realized_gain is the POST-block benchmark
# improvement measured AFTER the interference window, so it (and the raw post benchmark) is
# the outcome, not an input.
FORBIDDEN_FEATURES: dict[str, str] = {
    "realized_gain": "post-block benchmark improvement, measured AFTER the interference window — it IS the outcome, not a predictor",
    "post_benchmark": "the post-block benchmark value itself — the outcome",
    "expected_gain": "per-athlete zero-interference anchor derived from outcomes; a label normalizer, not a pre-block input",
    "gain_efficiency": "the supervised label itself (realized/expected ratio)",
    "label": "the supervised target itself",
    "better_direction": "benchmark orientation metadata used to sign the delta; folding it in would leak the label's sign, and it is not a training feature",
    "concurrent_load_other_episode": "interfering load from a DIFFERENT block is not this block's predictor",
}


def _as_frame(rows: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    return rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)


def build_frame(rows: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    """Build the supervised interference frame: concurrent load -> adaptation efficiency.

    Expects per-(athlete, episode) rows with ``athlete_id``, ``episode``,
    ``interference_pair``, ``concurrent_load`` (interfering-domain load in [0, 100]) and
    ``realized_gain`` (the benchmark improvement over the block, already oriented so that
    positive = better via ``better_direction``). For each athlete the expected uninterfered
    gain is the mean ``realized_gain`` over that athlete's LOW-interference anchor blocks
    (``z <= LOW_Z_ANCHOR``); ``gain_efficiency`` is ``realized_gain / expected_gain``.
    Athletes without enough anchors (or a non-positive expected gain) are dropped — their
    label cannot be normalized without leakage. Returns ``athlete_id``, ``episode``,
    ``interference_pair``, ``z_interfering_load`` and ``gain_efficiency``.
    """
    df = _as_frame(rows)
    df = df.sort_values([GROUP_COLUMN, PAIR_COLUMN, ORDER_COLUMN]).reset_index(drop=True)
    df["z_interfering_load"] = np.clip(df["concurrent_load"].to_numpy(dtype=float) / 100.0, 0.0, None)

    out_parts: list[pd.DataFrame] = []
    # Normalize within each (athlete, pair): the expectation is pair-specific because a
    # different interference pair targets a different adaptation axis.
    for (_aid, _pair), grp in df.groupby([GROUP_COLUMN, PAIR_COLUMN], sort=False):
        anchors = grp.loc[grp["z_interfering_load"] <= LOW_Z_ANCHOR, "realized_gain"]
        if len(anchors) < MIN_ANCHORS:
            continue
        expected = float(anchors.mean())
        if not np.isfinite(expected) or expected <= 1e-9:
            continue
        eff = grp["realized_gain"].to_numpy(dtype=float) / expected
        block = grp[[GROUP_COLUMN, ORDER_COLUMN, PAIR_COLUMN, "z_interfering_load"]].copy()
        block[LABEL_COLUMN] = np.clip(eff, _EFF_CLIP_LO, _EFF_CLIP_HI)
        out_parts.append(block)

    if not out_parts:
        return df.iloc[0:0][[GROUP_COLUMN, ORDER_COLUMN, PAIR_COLUMN, *FEATURE_COLUMNS, LABEL_COLUMN]]

    out = pd.concat(out_parts, ignore_index=True)
    out = out.sort_values([GROUP_COLUMN, PAIR_COLUMN, ORDER_COLUMN]).reset_index(drop=True)
    return out[[GROUP_COLUMN, ORDER_COLUMN, PAIR_COLUMN, *FEATURE_COLUMNS, LABEL_COLUMN]]


def grouped_time_split(
    frame: pd.DataFrame, holdout_frac: float = 0.25
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split holding out whole athletes (grouped), preserving per-athlete episode order.

    Athletes are partitioned by id so none appears in both train and test, which keeps the
    per-athlete zero-interference normalizer from leaking across the split. Mirrors
    ``app.ml.q2_recovery.build_training_frame.grouped_time_split``.
    """
    ids = np.sort(frame[GROUP_COLUMN].unique())
    n_holdout = max(1, int(round(len(ids) * holdout_frac)))
    test_ids = set(ids[-n_holdout:].tolist())
    is_test = frame[GROUP_COLUMN].isin(test_ids)
    order = [GROUP_COLUMN, PAIR_COLUMN, ORDER_COLUMN]
    train_df = frame[~is_test].sort_values(order).reset_index(drop=True)
    test_df = frame[is_test].sort_values(order).reset_index(drop=True)
    return train_df, test_df


def synthetic_interference_rows(
    *,
    pair: str = "endurance_on_strength",
    alpha_true: float = 1.8,
    floor: float = 0.30,
    n_athletes: int = 40,
    n_anchor_blocks: int = 4,
    n_load_blocks: int = 16,
    effect: float = 1.0,
    base_gain_range: tuple[float, float] = (4.0, 12.0),
    gain_noise: float = 0.06,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Generate a synthetic per-(athlete, block) fixture with a PLANTED suppression alpha.

    Each athlete has ``n_anchor_blocks`` zero-interference anchor blocks (z ~= 0) plus
    ``n_load_blocks`` blocks whose concurrent interfering load spreads over [0, ~100]. The
    realized gain is ``base_gain_a * efficiency(z) * (1 + noise)`` with

        efficiency(z) = floor + (1 - floor) * exp(-alpha_true * effect * z)

    so a higher concurrent load yields a proportionally SMALLER gain — the ADR-0037 signal.
    ``effect`` scales the suppression: ``effect=1`` plants the full ``alpha_true``;
    ``effect=0`` removes any z-dependence (efficiency == 1 for all z), the honest NO-SIGNAL
    null on which the gate must STAY SHADOW (a flat curve implies alpha ~= 0, which the
    ADR-0037 interference-floor guardrail refuses to promote).
    """
    rng = np.random.default_rng(seed)
    alpha_eff = float(alpha_true) * float(effect)
    rows: list[dict[str, Any]] = []
    for a in range(n_athletes):
        base_gain = float(rng.uniform(*base_gain_range))
        # Anchor blocks: essentially no concurrent interfering load.
        zs = list(np.abs(rng.normal(0.0, 1.5, n_anchor_blocks)))  # << LOW_Z_ANCHOR*100
        # Load blocks: half-normal spread so there is mass near 0 and a long tail toward 100.
        zs += list(np.clip(np.abs(rng.normal(0.0, 42.0, n_load_blocks)), 0.0, 100.0))
        for i, load in enumerate(zs):
            z = float(load) / 100.0
            efficiency = floor + (1.0 - floor) * float(np.exp(-alpha_eff * z))
            realized = base_gain * efficiency * (1.0 + float(rng.normal(0.0, gain_noise)))
            rows.append(
                {
                    "athlete_id": a,
                    "episode": i,
                    "interference_pair": pair,
                    "concurrent_load": float(load),
                    "realized_gain": realized,
                    "better_direction": "higher",
                }
            )
    return rows
