"""Band-limited Dynamic Time Warping for musical sequence alignment.

The implementation is memory-aware: the fine-resolution pass computes the cost
row-by-row inside a band around a coarse alignment, so a full ``N x M`` cost
matrix is never materialised. Free start/end handling allows for different
amounts of leading silence / trailing decay between the two performances.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import numpy as np
from scipy.spatial.distance import cdist

from .features import FeatureSet, downsample_features, sequence_for_alignment

RowCost = Callable[[int, int, int], np.ndarray]


@dataclass
class WarpResult:
    a_to_b: np.ndarray  # (Na,) float frame index in B for every A frame
    b_to_a: np.ndarray  # (Nb,) float frame index in A for every B frame
    ia: np.ndarray  # (K,) path A indices
    jb: np.ndarray  # (K,) path B indices
    normalized_cost: float
    band_width: int
    free_start: bool
    free_end: bool


def band_around_diagonal(n: int, m: int, width: int) -> Tuple[np.ndarray, np.ndarray]:
    """Return per-row [lo, hi] column bounds forming a diagonal band."""
    if n <= 1:
        return np.zeros(1, dtype=int), np.array([max(m - 1, 0)], dtype=int)
    rows = np.arange(n)
    center = rows * (m - 1) / float(n - 1)
    lo = np.maximum(0, np.ceil(center - width).astype(int))
    hi = np.minimum(m - 1, np.floor(center + width).astype(int))
    hi = np.maximum(hi, lo)
    lo = np.minimum(lo, hi)
    # Keep the (0, 0) start reachable and force the final column reachable, but
    # leave the first row open so free-start can absorb leading-silence offsets.
    lo[0] = 0
    lo[-1] = min(lo[-1], m - 1)
    hi[-1] = m - 1
    return lo, hi


def _backtrace(
    ptr: np.ndarray, lo: np.ndarray, start: Tuple[int, int]
) -> np.ndarray:
    path = []
    i, j = start
    while True:
        path.append((i, j))
        back = ptr[i, j - lo[i]]
        if back < 0:
            break
        if back == 0:
            i, j = i - 1, j - 1
        elif back == 1:
            i = i - 1
        else:
            j = j - 1
    path.reverse()
    return np.asarray(path, dtype=np.int64)


def dtw_banded(
    row_cost: RowCost,
    n: int,
    m: int,
    lo: np.ndarray,
    hi: np.ndarray,
    free_start: bool = True,
    free_end: bool = True,
    band_width: int = 0,
) -> WarpResult:
    """Banded DTW given a lazily-evaluated row cost function.

    ``row_cost(i, j0, j1)`` must return the cost for row ``i`` columns
    ``[j0, j1)``. When ``free_start``/``free_end`` are set the path may begin
    on the first row/column and end on the last row/column, which tolerates
    differing amounts of intro/outro material.
    """
    infinity = np.inf
    width = int(np.max(hi - lo)) + 1
    acc = np.full((n, width), infinity, dtype=np.float64)
    ptr = np.full((n, width), -1, dtype=np.int8)

    for i in range(n):
        lo_i = int(lo[i])
        hi_i = int(hi[i])
        costs = np.asarray(row_cost(i, lo_i, hi_i + 1), dtype=np.float64)
        row = acc[i]
        for k in range(costs.shape[0]):
            j = lo_i + k
            if costs[k] == infinity:
                continue
            if (i == 0 or j == 0) and (free_start or (i == 0 and j == 0)):
                row[k] = costs[k]
                ptr[i, k] = -1
                continue
            best = infinity
            bp = -1
            if i > 0 and lo[i - 1] <= j - 1 <= hi[i - 1]:
                v = acc[i - 1, (j - 1) - lo[i - 1]]
                if v < best:
                    best, bp = v, 0
            if i > 0 and lo[i - 1] <= j <= hi[i - 1]:
                v = acc[i - 1, j - lo[i - 1]]
                if v < best:
                    best, bp = v, 1
            if j - 1 >= lo_i:
                v = row[(j - 1) - lo_i]
                if v < best:
                    best, bp = v, 2
            if bp == -1:
                continue
            row[k] = costs[k] + best
            ptr[i, k] = bp

    # Choose the end cell.
    best_end = infinity
    end = (n - 1, m - 1)
    if free_end:
        for i in range(n):
            if lo[i] <= m - 1 <= hi[i]:
                v = acc[i, (m - 1) - lo[i]]
                if v < best_end:
                    best_end, end = v, (i, m - 1)
        for k in range(acc.shape[1]):
            j = int(lo[n - 1]) + k
            if 0 <= j <= m - 1:
                v = acc[n - 1, k]
                if v < best_end:
                    best_end, end = v, (n - 1, j)
    else:
        if lo[n - 1] <= m - 1 <= hi[n - 1]:
            best_end = acc[n - 1, (m - 1) - lo[n - 1]]
    if not np.isfinite(best_end):
        # Fall back to a straight diagonal mapping if no path was found.
        a_to_b = np.linspace(0, max(m - 1, 0), n)
        b_to_a = np.linspace(0, max(n - 1, 0), m)
        return WarpResult(
            a_to_b=a_to_b,
            b_to_a=b_to_a,
            ia=np.arange(n),
            jb=np.round(a_to_b).astype(np.int64),
            normalized_cost=infinity,
            band_width=band_width,
            free_start=free_start,
            free_end=free_end,
        )

    path = _backtrace(ptr, lo, end)
    ia = path[:, 0]
    jb = path[:, 1]
    keep = np.ones(ia.shape[0], dtype=bool)
    keep[1:] = np.diff(ia) > 0
    ia_u = ia[keep]
    jb_u = jb[keep]
    if ia_u.size < 2:
        ia_u = np.array([0, max(n - 1, 1)])
        jb_u = np.array([int(jb[0]), int(jb[-1])])
    a_axis = np.arange(n)
    a_to_b = np.interp(a_axis, ia_u, jb_u.astype(np.float64))
    # Symmetric mapping for B -> A.
    keep_b = np.ones(jb.shape[0], dtype=bool)
    keep_b[1:] = np.diff(jb) > 0
    jb_b = jb[keep_b]
    ia_b = ia[keep_b]
    if jb_b.size < 2:
        jb_b = np.array([0, max(m - 1, 1)])
        ia_b = np.array([int(ia[0]), int(ia[-1])])
    b_axis = np.arange(m)
    b_to_a = np.interp(b_axis, jb_b, ia_b.astype(np.float64))

    normalized_cost = float(best_end) / max(path.shape[0], 1)
    return WarpResult(
        a_to_b=a_to_b,
        b_to_a=b_to_a,
        ia=ia,
        jb=jb,
        normalized_cost=normalized_cost,
        band_width=band_width,
        free_start=free_start,
        free_end=free_end,
    )


def _seq_cost_matrix(seq_a: np.ndarray, seq_b: np.ndarray) -> np.ndarray:
    return cdist(seq_a, seq_b, metric="euclidean").astype(np.float64)


def refine_band_from_mapping(
    mapping: np.ndarray, m: int, half_width: int
) -> Tuple[np.ndarray, np.ndarray]:
    lo = np.maximum(0, np.floor(mapping - half_width).astype(int))
    hi = np.minimum(m - 1, np.ceil(mapping + half_width).astype(int))
    hi = np.maximum(hi, lo)
    lo = np.minimum(lo, hi)
    return lo, hi


def align_feature_sets(
    feat_a: FeatureSet,
    feat_b: FeatureSet,
    target_frames: int = 1200,
    band_ratio: float = 0.25,
    onset_weight: float = 0.6,
    refine: bool = True,
    refine_half_width_frames: int = 60,
    free_start: bool = True,
    free_end: bool = True,
) -> WarpResult:
    """Hierarchical alignment: coarse global DTW + fine banded refinement."""
    if feat_a.n_frames < 2 or feat_b.n_frames < 2:
        raise ValueError("Both feature sets need at least two frames to align.")

    seq_a = sequence_for_alignment(feat_a, onset_weight)
    seq_b = sequence_for_alignment(feat_b, onset_weight)
    na, nb = seq_a.shape[0], seq_b.shape[0]

    factor = max(1, int(np.ceil(max(na, nb) / float(max(target_frames, 32)))))
    coarse_a = downsample_features(feat_a, factor)
    coarse_b = downsample_features(feat_b, factor)
    cseq_a = sequence_for_alignment(coarse_a, onset_weight)
    cseq_b = sequence_for_alignment(coarse_b, onset_weight)
    cna, cnb = cseq_a.shape[0], cseq_b.shape[0]

    coarse_cost = _seq_cost_matrix(cseq_a, cseq_b)
    width = max(int(round(band_ratio * max(cna, cnb))), 16)
    lo, hi = band_around_diagonal(cna, cnb, width)

    def coarse_row(i: int, j0: int, j1: int) -> np.ndarray:
        return coarse_cost[i, j0:j1]

    coarse = dtw_banded(
        coarse_row, cna, cnb, lo, hi, free_start=free_start, free_end=free_end, band_width=width
    )

    if not refine:
        mapping = np.interp(
            np.arange(na),
            np.arange(cna) * factor,
            coarse.a_to_b * factor,
        )
        a_to_b = np.clip(mapping, 0, nb - 1)
        mapping_b = np.interp(
            np.arange(nb),
            np.arange(cnb) * factor,
            coarse.b_to_a * factor,
        )
        b_to_a = np.clip(mapping_b, 0, na - 1)
        return WarpResult(
            a_to_b=a_to_b,
            b_to_a=b_to_a,
            ia=coarse.ia * factor,
            jb=coarse.jb * factor,
            normalized_cost=coarse.normalized_cost,
            band_width=width,
            free_start=coarse.free_start,
            free_end=coarse.free_end,
        )

    # Scale the coarse mapping to full resolution to build a fine band.
    fine_center = np.interp(
        np.arange(na), np.arange(cna) * factor, coarse.a_to_b * factor
    )
    half = max(refine_half_width_frames, 4 * factor)
    flo, fhi = refine_band_from_mapping(fine_center, nb, half)

    def fine_row(i: int, j0: int, j1: int) -> np.ndarray:
        return cdist(seq_a[i : i + 1], seq_b[j0:j1], metric="euclidean")[0]

    fine = dtw_banded(
        fine_row, na, nb, flo, fhi, free_start=free_start, free_end=free_end, band_width=half
    )
    if not np.isfinite(fine.normalized_cost):
        return coarse
    return fine
