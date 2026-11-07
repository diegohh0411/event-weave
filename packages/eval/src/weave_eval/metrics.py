"""Coreference metrics (MUC, B-cubed, CEAF-e, BLANC, CoNLL F1), checked against the reference scorer v8.01.

Mentions present on only one side are not aligned or removed: a key mention missing from the response lowers recall,
a response mention missing from the key lowers precision (the reference scorer's handling of twinless mentions).
Everything is computed from the key x response contingency table, so cost is linear in the number of mentions.
Counts add up across documents/topics; `scores` turns summed counts into percentages (0-100).
"""
from collections import Counter, defaultdict
from collections.abc import Hashable, Mapping
from dataclasses import dataclass, fields

import numpy as np
from scipy.optimize import linear_sum_assignment

Clustering = Mapping[Hashable, Hashable]  # mention -> cluster label


@dataclass(frozen=True)
class Counts:
    muc_rn: int = 0
    muc_rd: int = 0
    muc_pn: int = 0
    muc_pd: int = 0
    b3_rn: float = 0.0
    b3_rd: int = 0
    b3_pn: float = 0.0
    b3_pd: int = 0
    ceaf_sim: float = 0.0
    ceaf_k: int = 0
    ceaf_r: int = 0
    blanc_cc: int = 0  # correct coreference links
    blanc_kc: int = 0  # key coreference links
    blanc_rc: int = 0  # response coreference links
    blanc_cn: int = 0  # correct non-coreference links
    blanc_kn: int = 0
    blanc_rn: int = 0

    def __add__(self, other: "Counts") -> "Counts":
        return Counts(**{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(self)})

    @staticmethod
    def zero() -> "Counts":
        return Counts()


@dataclass(frozen=True)
class PRF:
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True)
class Scores:
    muc: PRF
    b3: PRF
    ceafe: PRF
    blanc: PRF
    conll: float


def _pairs(n: int) -> int:
    return n * (n - 1) // 2


def _sizes(clustering: Clustering) -> Counter:
    return Counter(clustering.values())


def _overlap(key: Clustering, response: Clustering) -> Counter:
    """n[(key label, response label)] over mentions present on both sides."""
    cells: Counter = Counter()
    for m, k in key.items():
        if m in response:
            cells[(k, response[m])] += 1
    return cells


def _ceafe_component(rows: list, cols: list, cells: Counter, ksize: Counter, rsize: Counter) -> float:
    sim = np.zeros((len(rows), len(cols)))
    ri = {k: i for i, k in enumerate(rows)}
    ci = {r: j for j, r in enumerate(cols)}
    for (k, r), n in cells.items():
        sim[ri[k], ci[r]] = 2 * n / (ksize[k] + rsize[r])
    i, j = linear_sum_assignment(sim, maximize=True)
    return float(sim[i, j].sum())


def ceafe_similarity(key: Clustering, response: Clustering, by_component: bool = True) -> float:
    """Total phi4 similarity of the best one-to-one entity alignment.

    The default solves each connected component of the key-response overlap graph on its own (entities with no
    overlap add 0), which gives the same total as one assignment over the full matrix but stays fast on large pools.
    """
    ksize, rsize = _sizes(key), _sizes(response)
    cells = _overlap(key, response)
    if not cells:
        return 0.0
    if not by_component:
        return _ceafe_component(list(ksize), list(rsize), cells, ksize, rsize)
    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for k, r in cells:
        parent[find(("k", k))] = find(("r", r))
    members: dict = defaultdict(Counter)
    for (k, r), n in cells.items():
        members[find(("k", k))][(k, r)] = n
    total = 0.0
    for comp in members.values():
        rows = list(dict.fromkeys(k for k, _ in comp))
        cols = list(dict.fromkeys(r for _, r in comp))
        total += _ceafe_component(rows, cols, comp, ksize, rsize)
    return total


def counts(key: Clustering, response: Clustering) -> Counts:
    """Numerators and denominators of every metric for one scored clustering (a document, a topic or a pool)."""
    ksize, rsize = _sizes(key), _sizes(response)
    cells = _overlap(key, response)
    n_key, n_resp = len(key), len(response)
    shared = sum(cells.values())

    # MUC: links are (size - 1) per chain; a correct link joins mentions sharing both a key and a response chain.
    muc_correct = shared - len(cells)

    # B-cubed: per mention, |K n R| / |K| (recall) and |K n R| / |R| (precision); twinless response mentions give 0.
    b3_rn = sum(n * n / ksize[k] for (k, _), n in cells.items())
    b3_pn = sum(n * n / rsize[r] for (_, r), n in cells.items())

    # BLANC (Luo et al. 2014): non-coreference links are pairs in different chains of the same side.
    kc = sum(_pairs(s) for s in ksize.values())
    rc = sum(_pairs(s) for s in rsize.values())
    cc = sum(_pairs(n) for n in cells.values())
    in_k: Counter = Counter()
    in_r: Counter = Counter()
    for (k, r), n in cells.items():
        in_k[k] += n
        in_r[r] += n
    cn = _pairs(shared) - sum(_pairs(n) for n in in_k.values()) - sum(_pairs(n) for n in in_r.values()) + cc

    return Counts(
        muc_rn=muc_correct, muc_rd=n_key - len(ksize), muc_pn=muc_correct, muc_pd=n_resp - len(rsize),
        b3_rn=b3_rn, b3_rd=n_key, b3_pn=b3_pn, b3_pd=n_resp,
        ceaf_sim=ceafe_similarity(key, response), ceaf_k=len(ksize), ceaf_r=len(rsize),
        blanc_cc=cc, blanc_kc=kc, blanc_rc=rc,
        blanc_cn=cn, blanc_kn=_pairs(n_key) - kc, blanc_rn=_pairs(n_resp) - rc,
    )


def _div(n: float, d: float) -> float:
    return n / d if d else 0.0


def _f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


def _prf(rn: float, rd: float, pn: float, pd: float) -> PRF:
    p, r = _div(pn, pd), _div(rn, rd)
    return PRF(100 * p, 100 * r, 100 * _f1(p, r))


def _blanc(c: Counts) -> PRF:
    """Mean of the coreference and non-coreference scores; a side with no key links is left out (v8.01)."""
    pa, ra = _div(c.blanc_cc, c.blanc_rc), _div(c.blanc_cc, c.blanc_kc)
    pn, rn = _div(c.blanc_cn, c.blanc_rn), _div(c.blanc_cn, c.blanc_kn)
    fa, fn = _f1(pa, ra), _f1(pn, rn)
    if c.blanc_kc == 0 and c.blanc_kn == 0:
        return PRF(0.0, 0.0, 0.0)
    if c.blanc_kc == 0:
        return PRF(100 * pn, 100 * rn, 100 * fn)
    if c.blanc_kn == 0:
        return PRF(100 * pa, 100 * ra, 100 * fa)
    return PRF(100 * (pa + pn) / 2, 100 * (ra + rn) / 2, 100 * (fa + fn) / 2)


def scores(c: Counts) -> Scores:
    muc = _prf(c.muc_rn, c.muc_rd, c.muc_pn, c.muc_pd)
    b3 = _prf(c.b3_rn, c.b3_rd, c.b3_pn, c.b3_pd)
    ceafe = _prf(c.ceaf_sim, c.ceaf_k, c.ceaf_sim, c.ceaf_r)
    return Scores(muc=muc, b3=b3, ceafe=ceafe, blanc=_blanc(c), conll=(muc.f1 + b3.f1 + ceafe.f1) / 3)
