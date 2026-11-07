"""Seeded topic bootstrap: 95% percentile intervals of F1, resampling (language, topic) units with replacement."""
from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Hashable
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from weave_eval.metrics import METRICS, Counts, counts, score_matrix, to_matrix
from weave_eval.tracks import Track2Inputs

Interval = dict[str, tuple[float, float]]


def draw(units: list, resamples: int, seed: int) -> np.ndarray:
    """Indices into sorted(units), shape (resamples, len(units)); all drawn up front from one seeded generator.

    numpy rejects negative seeds but RunConfig.seed is any int, so the seed is taken mod 2**64 (deterministic)."""
    n = len(units)
    return np.random.default_rng(seed % 2**64).integers(0, n, size=(resamples, n))


def _interval(sums: np.ndarray) -> Interval:
    r = score_matrix(sums)
    out = {}
    for m in METRICS:
        f1 = r[m] if m == "conll" else r[m][2]
        lo, hi = np.percentile(f1, [2.5, 97.5])
        out[m] = (float(lo), float(hi))
    return out


def interval_additive(rows: dict[Hashable, Counts], resamples: int, seed: int) -> Interval:
    """Intervals where a resample's Counts are the sum of its units' Counts (Track 1)."""
    units = sorted(rows)
    if not units:
        return {m: (0.0, 0.0) for m in METRICS}
    mat = to_matrix([rows[u] for u in units])
    idx = draw(units, resamples, seed)
    return _interval(mat[idx].sum(axis=1))


def _score_chunk(unit_data: list, idx: np.ndarray) -> np.ndarray:
    """Summed-count matrix for each resample in idx. unit_data[u] = [(mention id, key label, response label|None)].

    Occurrence j of a unit is its j-th repeat in the resample: copies with the same j stay together across topics,
    and repeats of one topic never merge with each other.
    """
    out = []
    for row in idx:
        key: dict = {}
        resp: dict = {}
        seen: dict = {}
        for u in row:
            j = seen.get(u, 0)  # j-th repeat of this topic in the resample
            seen[u] = j + 1
            for mid, k, r in unit_data[u]:
                key_id = (mid, j)
                if k is not None:
                    key[key_id] = (k, j)
                if r is not None:
                    resp[key_id] = (r, j)
        out.append(to_matrix([counts(key, resp)])[0])
    return np.array(out).reshape(len(idx), -1)


def interval_pooled(inputs: Track2Inputs, language: str | None, resamples: int, seed: int,
                    workers: int | None = None) -> Interval:
    """Intervals where each resample is one clustering over the drawn topics (Track 2). language=None pools all."""
    workers = workers or os.cpu_count() or 1
    by_unit: dict = defaultdict(list)
    for mid in inputs.group.keys() | inputs.key.keys() | inputs.response.keys():
        unit = inputs.group[mid]
        if language is None or unit[0] == language:
            by_unit[unit].append((mid, inputs.key.get(mid), inputs.response.get(mid)))
    units = sorted(by_unit)
    if not units:
        return {m: (0.0, 0.0) for m in METRICS}
    unit_data = [sorted(by_unit[u], key=lambda t: repr(t[0])) for u in units]
    idx = draw(units, resamples, seed)
    if workers <= 1 or resamples <= 1:
        sums = _score_chunk(unit_data, idx)
    else:
        chunks = np.array_split(idx, min(workers, resamples))
        with ProcessPoolExecutor(max_workers=workers) as ex:
            sums = np.concatenate(list(ex.map(_score_chunk, [unit_data] * len(chunks), chunks)))
    return _interval(sums)
