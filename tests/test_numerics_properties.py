"""Property-based tests for ``app.logic.ekf.numerics`` (INT-16).

The module's docstring claims covariances "must stay symmetric and PSD through
many predict/update steps" — a universal claim previously verified only by
example-based anecdotes (including a single 50-iteration scripted loop with one
fixed seed and one profile in ``test_ekf_update.py``). These property families
generate matrices across the input space each helper claims to handle, rather
than one scripted trajectory. They complement — do not replace — that replay
matrix; any minimized counterexample hypothesis finds is pinned below as a
deterministic regression fixture rather than folded back into the shrunk
random search.
"""
from __future__ import annotations

import math
import os

import numpy as np
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays
from psd_helpers import assert_covariance_psd, assert_covariance_symmetric

from app.logic.ekf.numerics import clip_diagonal, nearest_psd, stabilize, symmetrize

# Reproducible CI seeds: "ci" derandomizes so a failure is deterministically
# replayable from the same code, without needing to capture/share a seed by hand.
settings.register_profile("ci", deadline=None, max_examples=200, derandomize=True)
settings.register_profile("dev", deadline=None, max_examples=50)
settings.load_profile("ci" if os.environ.get("CI") else "dev")

# Small dims keep shrinking tractable (per the task's own guidance).
DIMS = st.integers(min_value=3, max_value=6)
_ELEMENTS = st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False)


@st.composite
def square_matrices(draw) -> np.ndarray:
    n = draw(DIMS)
    flat = draw(arrays(dtype=np.float64, shape=n * n, elements=_ELEMENTS))
    return flat.reshape(n, n)


@st.composite
def symmetric_matrices(draw) -> np.ndarray:
    return symmetrize(draw(square_matrices()))


@st.composite
def psd_matrices(draw) -> np.ndarray:
    """PSD by construction: ``A @ A.T`` is PSD for any real ``A``."""
    A = draw(square_matrices())
    return A @ A.T


@st.composite
def well_conditioned_psd_matrices(draw, *, eps: float = 1e-3) -> np.ndarray:
    """PSD with a *known* eigenvalue floor (``>= eps``) via Weyl's inequality,
    so ``nearest_psd``'s default floor (1e-9) is guaranteed to be a true no-op."""
    P = draw(psd_matrices())
    return P + eps * np.eye(P.shape[0])


@st.composite
def diagonal_bounds(draw, dim: int) -> tuple[np.ndarray, np.ndarray]:
    lo = draw(
        arrays(dtype=np.float64, shape=dim, elements=st.floats(min_value=1e-3, max_value=5.0, allow_nan=False))
    )
    width = draw(
        arrays(dtype=np.float64, shape=dim, elements=st.floats(min_value=0.0, max_value=5.0, allow_nan=False))
    )
    return lo, lo + width


@st.composite
def psd_with_bounds(draw) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    P = draw(psd_matrices())
    lo, hi = draw(diagonal_bounds(P.shape[0]))
    return P, lo, hi


# --- symmetrize --------------------------------------------------------------

@given(M=square_matrices())
def test_symmetrize_output_is_symmetric(M):
    assert_covariance_symmetric(symmetrize(M))


@given(M=square_matrices())
def test_symmetrize_is_idempotent(M):
    once = symmetrize(M)
    twice = symmetrize(once)
    assert np.allclose(once, twice, atol=1e-9)


@given(M=symmetric_matrices())
def test_symmetrize_preserves_already_symmetric_input(M):
    assert np.allclose(symmetrize(M), M, atol=1e-9)


# --- nearest_psd ---------------------------------------------------------------

@given(M=square_matrices())
def test_nearest_psd_output_is_symmetric(M):
    assert_covariance_symmetric(nearest_psd(M))


@given(M=square_matrices())
def test_nearest_psd_respects_the_floor(M):
    floor = 1e-6
    out = nearest_psd(M, floor=floor)
    assert np.min(np.linalg.eigvalsh(out)) >= floor - 1e-9


@given(M=square_matrices())
def test_nearest_psd_is_idempotent(M):
    floor = 1e-6
    once = nearest_psd(M, floor=floor)
    twice = nearest_psd(once, floor=floor)
    assert np.allclose(once, twice, atol=1e-6)


@given(M=well_conditioned_psd_matrices())
def test_nearest_psd_is_fixed_point_on_already_psd_input(M):
    assert np.allclose(nearest_psd(M), symmetrize(M), atol=1e-6)


# --- clip_diagonal: the highest-value previously-untested claim in the repo ----

@given(data=psd_with_bounds())
def test_clip_diagonal_diag_within_bounds(data):
    P, lo, hi = data
    assume(np.all(np.diag(P) > 1e-6))  # stay away from the internal 1e-12 floor edge
    out = clip_diagonal(P, lo, hi)
    d = np.diag(out)
    assert np.all(d >= lo - 1e-9)
    assert np.all(d <= hi + 1e-9)


@given(data=psd_with_bounds())
def test_clip_diagonal_preserves_correlation_coefficients(data):
    """The entire point of the outer-product rescaling: corr(P') == corr(P),
    for every axis pair — not only the pairs that weren't clamped, since the
    congruence scale[i]*scale[j] cancels out of the correlation ratio exactly
    whether or not axis i or j was clamped."""
    P, lo, hi = data
    d = np.diag(P)
    assume(np.all(d > 1e-6))
    out = clip_diagonal(P, lo, hi)
    d_out = np.diag(out)
    n = P.shape[0]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            denom_before = math.sqrt(d[i] * d[j])
            denom_after = math.sqrt(d_out[i] * d_out[j])
            if denom_before < 1e-9 or denom_after < 1e-9:
                continue
            corr_before = P[i, j] / denom_before
            corr_after = out[i, j] / denom_after
            assert corr_after == pytest.approx(corr_before, abs=1e-6)


@given(data=psd_with_bounds())
def test_clip_diagonal_stays_psd_even_with_asymmetric_clamping(data):
    """P' = D P D^T (D diagonal) preserves PSD-ness for ANY real D — including
    per-axis scales that clamp different axes up vs. down — because
    x^T (D P D^T) x = (D^T x)^T P (D^T x) >= 0 whenever P is PSD."""
    P, lo, hi = data
    assume(np.all(np.diag(P) > 1e-6))
    out = clip_diagonal(P, lo, hi)
    assert np.min(np.linalg.eigvalsh(symmetrize(out))) >= -1e-6


# --- stabilize -----------------------------------------------------------------

@given(M=square_matrices())
def test_stabilize_output_is_symmetric_and_psd(M):
    n = M.shape[0]
    lo, hi = np.full(n, 0.1), np.full(n, 5.0)
    assert_covariance_psd(stabilize(M, lo, hi))


# GENUINE DEFECT found via property testing (INT-16) — NOT weakened away.
#
# stabilize()'s docstring implies the diagonal ends in [lo, hi] ("Symmetrize,
# clamp diagonal into [lo, hi], then project to PSD"). That is false in
# general: clip_diagonal() DOES land the diagonal in [lo, hi], but the
# subsequent nearest_psd() eigenvalue-floor projection is a *global* symmetric
# reconstruction that can push individual diagonal entries back outside the
# range it was just clamped into, whenever the input has a negative eigenvalue
# large enough to need correcting. The violation grows with how much
# correction nearest_psd has to do, so it is not just float-noise-scale.
#
# Minimized deterministic example (no randomness — reproducible on any machine):
#   P = [[1.0, 1.9], [1.9, 1.0]]   (eigenvalues -0.9, 2.9 — one strongly negative)
#   lo = hi = [1.2, 1.2] band width -> clip_diagonal is a no-op here (diag already
#   in [0.5, 1.2]... see values below), then nearest_psd's PSD projection raises
#   both diagonal entries to 1.45, exceeding hi=1.2 by 0.25.
#
# This is a real gap in the shipped numerics, reported to the Critic/Orchestrator
# per the INT-16 handoff rather than silently patched (numerics.py's existing
# numerical behavior is out of scope for this task).
@pytest.mark.xfail(
    strict=True,
    reason=(
        "INT-16 genuine defect: stabilize()'s final nearest_psd() projection can "
        "push a diagonal entry outside the [lo, hi] band that clip_diagonal() just "
        "enforced, when correcting a sufficiently negative input eigenvalue. "
        "Reported to Critic/Orchestrator; not fixed here (out of task scope)."
    ),
)
def test_stabilize_diagonal_stays_in_bounds_KNOWN_BUG():
    P = np.array([[1.0, 1.9], [1.9, 1.0]])
    lo = np.array([0.5, 0.5])
    hi = np.array([1.2, 1.2])
    out = stabilize(P, lo, hi)
    d = np.diag(out)
    assert np.all(d <= hi + 1e-9), f"diagonal {d} exceeds hi={hi} after PSD projection"


# --- finiteness and dimension-order invariance ---------------------------------

@given(M=square_matrices())
def test_stabilize_output_is_finite(M):
    n = M.shape[0]
    lo, hi = np.full(n, 0.1), np.full(n, 5.0)
    assert np.all(np.isfinite(stabilize(M, lo, hi)))


@given(data=psd_with_bounds(), perm_seed=st.integers(min_value=0, max_value=10_000))
def test_stabilize_is_equivariant_under_axis_permutation(data, perm_seed):
    P, lo, hi = data
    n = P.shape[0]
    perm = np.random.RandomState(perm_seed).permutation(n)
    invperm = np.argsort(perm)

    out = stabilize(P, lo, hi)
    out_perm = stabilize(P[np.ix_(perm, perm)], lo[perm], hi[perm])
    recovered = out_perm[np.ix_(invperm, invperm)]
    assert np.allclose(out, recovered, atol=1e-6)
