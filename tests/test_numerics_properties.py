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


@st.composite
def any_matrix_with_bounds(draw) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Unconstrained (typically *indefinite*) inputs — stabilize()'s real contract.

    Deliberately NOT restricted to PSD: the INT-16 defect only fired when the PSD
    projection had genuine correction work to do, which a PSD-by-construction
    strategy can never provoke.
    """
    M = draw(square_matrices())
    lo, hi = draw(diagonal_bounds(M.shape[0]))
    return M, lo, hi


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


@given(data=any_matrix_with_bounds())
def test_stabilize_holds_psd_and_diagonal_bounds_simultaneously(data):
    """INT-16's core claim: stabilize() delivers BOTH invariants at once.

    Asserting them together is the point. Each held on its own before the fix —
    it was their *conjunction* that failed, because the final PSD projection
    silently undid the diagonal clamp that ran before it.
    """
    M, lo, hi = data
    out = stabilize(M, lo, hi)

    assert_covariance_psd(out)  # symmetric AND min eigenvalue >= -tol
    d = np.diag(out)
    assert np.all(d >= lo - 1e-9), f"diagonal {d} below lo={lo}"
    assert np.all(d <= hi + 1e-9), f"diagonal {d} above hi={hi}"


@given(data=any_matrix_with_bounds())
def test_stabilize_lands_diagonal_on_the_band_edge_when_clamping_bites(data):
    """Sharper than "within [lo, hi]": the diagonal is the *exact* clamp of the
    PSD-projected diagonal, so a clamped axis sits precisely on its band edge
    rather than merely somewhere inside. This is what pins the ordering — the
    old order could only satisfy this by accident."""
    M, lo, hi = data
    expected = np.clip(np.diag(nearest_psd(symmetrize(M))), lo, hi)
    assert np.allclose(np.diag(stabilize(M, lo, hi)), expected, rtol=1e-9, atol=1e-12)


# REGRESSION FIXTURE for the INT-16 defect, now FIXED — keep this pinned.
#
# The defect: stabilize() used to run `nearest_psd(clip_diagonal(...))`, projecting
# to the PSD cone LAST. nearest_psd is a *global* eigen-reconstruction, so it could
# push individual diagonal entries back outside the band clip_diagonal had just
# enforced, whenever the input had a negative eigenvalue large enough to need
# correcting. The violation scaled with the size of that correction — it was never
# float-noise-scale (a random sweep saw band overshoots up to ~3e20).
#
# The fix: swap the order to `clip_diagonal(nearest_psd(...), lo, hi)`. clip_diagonal
# is a congruence D P D by a non-negative diagonal D, which cannot introduce a
# negative eigenvalue, so clamping last preserves the PSD projection while landing
# the diagonal on [lo, hi] exactly. See stabilize()'s docstring for the trade-off.
#
# Deterministic minimized example (no randomness — reproducible on any machine):
#   P = [[1.0, 1.9], [1.9, 1.0]]   (eigenvalues -0.9, 2.9 — one strongly negative)
#   lo, hi = [0.5, 0.5], [1.2, 1.2]
# Under the old order, nearest_psd raised both diagonal entries to 1.45, exceeding
# hi = 1.2 by 0.25. Under the fixed order the diagonal lands on hi exactly.
def test_stabilize_diagonal_stays_in_bounds_regression_int16():
    P = np.array([[1.0, 1.9], [1.9, 1.0]])
    lo = np.array([0.5, 0.5])
    hi = np.array([1.2, 1.2])

    # Precondition: this input is exactly the pathological shape — strongly indefinite,
    # so the PSD projection has real correction work to do (that is what used to break
    # the band). If this ever stops holding, the fixture has stopped testing the defect.
    assert np.min(np.linalg.eigvalsh(P)) < -0.5, "fixture must stay strongly indefinite"

    out = stabilize(P, lo, hi)
    d = np.diag(out)

    # The invariant that used to fail: 1.45 > hi = 1.2 under the old ordering.
    assert np.all(d <= hi + 1e-9), f"diagonal {d} exceeds hi={hi} after PSD projection"
    assert np.all(d >= lo - 1e-9), f"diagonal {d} is below lo={lo} after PSD projection"
    # ...and the invariant the old ordering bought with it must still hold.
    assert_covariance_psd(out)


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
