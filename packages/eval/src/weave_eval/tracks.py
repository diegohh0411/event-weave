"""Track 1 (gold mentions, within and cross document), Track 2 (end to end) and trigger scores."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from weave_eval.gold import Gold, MentionKey
from weave_eval.metrics import PRF, Counts, counts
from weave_eval.system import SystemMention

TRACK2_MENTION_POLICY = (
    "Mentions present on only one side are kept as the reference CoNLL scorer (v8.01) keeps them: "
    "a missed gold mention lowers recall, a spurious system mention lowers precision.")

SKIPPED = {("hi", "cross"): "not scored: the dataset's Hindi crossDoc pairs have no topic or mention ids"}

Topic = tuple[str, str]  # (language, topic)


def _cross_skipped() -> set[str]:
    return {lang for lang, kind in SKIPPED if kind == "cross"}


@dataclass
class Track1Result:
    within: dict[Topic, Counts] = field(default_factory=dict)
    cross: dict[Topic, Counts] = field(default_factory=dict)
    unmatched_system: int = 0


def track1(gold: Gold, system: list[SystemMention]) -> dict[str, Track1Result]:
    """A gold mention with no clustered system mention is absent from the response (a miss)."""
    skip = _cross_skipped()
    by_key: dict[MentionKey, SystemMention] = {}
    for s in sorted(system, key=lambda s: (s.cluster_id is None, s.mention_id)):
        by_key.setdefault((s.document_id, s.start, s.end), s)
    doc_keys: dict[str, list[MentionKey]] = defaultdict(list)
    for k in gold.mentions:
        doc_keys[k[0]].append(k)
    doc_topic: dict[str, Topic] = {d: (gold.mentions[ks[0]].language, gold.mentions[ks[0]].topic)
                                   for d, ks in doc_keys.items()}
    topic_keys: dict[Topic, list[MentionKey]] = defaultdict(list)
    for d, ks in doc_keys.items():
        topic_keys[doc_topic[d]].extend(ks)

    def score(keys):
        key = {k: gold.cluster[k] for k in keys}
        resp = {k: by_key[k].cluster_id for k in keys if k in by_key and by_key[k].cluster_id is not None}
        return counts(key, resp)

    out: dict[str, Track1Result] = {}
    for lang, topic in sorted({t for t in doc_topic.values()}):
        res = out.setdefault(lang, Track1Result())
        total = Counts.zero()
        for d, t in doc_topic.items():
            if t == (lang, topic):
                total = total + score(doc_keys[d])
        res.within[(lang, topic)] = total
        if lang not in skip:
            res.cross[(lang, topic)] = score(topic_keys[(lang, topic)])
    for s in system:
        if (s.document_id, s.start, s.end) not in gold.mentions and s.document_id in gold.documents:
            out.setdefault(gold.documents[s.document_id][0], Track1Result()).unmatched_system += 1
    return out


@dataclass
class Track2Inputs:
    key: dict = field(default_factory=dict)
    response: dict = field(default_factory=dict)
    group: dict = field(default_factory=dict)  # key id -> (language, topic), the bootstrap unit


@dataclass
class Track2Result:
    per_language: dict[str, Counts]
    pooled: Counts
    inputs: Track2Inputs
    unaligned_gold: int


def track2(gold: Gold, system: list[SystemMention]) -> Track2Result:
    skip = _cross_skipped()
    doc_info = gold.documents
    scored = {d for d, (lang, _) in doc_info.items() if lang not in skip}

    spans: dict[tuple, list[SystemMention]] = defaultdict(list)  # clustered system mentions by token span
    for s in system:
        if s.document_id in scored and s.cluster_id is not None:
            spans[(s.document_id, s.tok_start, s.tok_end)].append(s)
    for v in spans.values():
        v.sort(key=lambda s: s.mention_id)

    inp = Track2Inputs()
    unaligned = 0
    taken: set[str] = set()
    for k, m in gold.mentions.items():
        if m.language in skip:
            continue
        if m.tok_start is None:
            unaligned += 1
            kid = ("gold", *k)
        else:
            kid = (m.document_id, m.tok_start, m.tok_end)
            if kid in spans:
                s = spans[kid][0]
                taken.add(s.mention_id)
                inp.response[kid] = s.cluster_id
        inp.key[kid] = gold.cluster[k]
        inp.group[kid] = (m.language, m.topic)
    for v in spans.values():
        for s in v:
            if s.mention_id not in taken:
                kid = ("sys", s.mention_id)
                inp.response[kid] = s.cluster_id
                inp.group[kid] = doc_info[s.document_id]

    per_language = {}
    for lang in sorted({g[0] for g in inp.group.values()}):
        ks = {i: c for i, c in inp.key.items() if inp.group[i][0] == lang}
        rs = {i: c for i, c in inp.response.items() if inp.group[i][0] == lang}
        per_language[lang] = counts(ks, rs)
    return Track2Result(per_language, counts(inp.key, inp.response), inp, unaligned)


def trigger_scores(gold: Gold, system: list[SystemMention]) -> dict[str, PRF]:
    """Exact token span, one-to-one: duplicates of a span beyond the first are false positives."""
    doc_lang = {d: lang for d, (lang, _) in gold.documents.items()}
    avail: dict[tuple, int] = defaultdict(int)
    n_sys: dict[str, int] = defaultdict(int)
    for s in system:
        if s.document_id in doc_lang:
            avail[(s.document_id, s.tok_start, s.tok_end)] += 1
            n_sys[doc_lang[s.document_id]] += 1
    n_gold: dict[str, int] = defaultdict(int)
    tp: dict[str, int] = defaultdict(int)
    for m in gold.mentions.values():
        n_gold[m.language] += 1
        span = (m.document_id, m.tok_start, m.tok_end)
        if m.tok_start is not None and avail[span] > 0:
            avail[span] -= 1
            tp[m.language] += 1

    def prf(t, g, s):
        p, r = (t / s if s else 0.0), (t / g if g else 0.0)
        return PRF(100 * p, 100 * r, 100 * (2 * p * r / (p + r) if p + r else 0.0))

    out = {lang: prf(tp[lang], n_gold[lang], n_sys[lang]) for lang in sorted(n_gold)}
    out["all"] = prf(sum(tp.values()), sum(n_gold.values()), sum(n_sys.values()))
    return out


@dataclass(frozen=True)
class Spread:
    clusters: int
    multi_topic: int
    multi_language: int


def cluster_spread(system: list[SystemMention], gold: Gold) -> Spread:
    """Clusters with a member in a scored document; topic and language come from that document's gold row."""
    doc_info = gold.documents
    members: dict[str, set[Topic]] = defaultdict(set)
    for s in system:
        if s.cluster_id is not None and s.document_id in doc_info:
            members[s.cluster_id].add(doc_info[s.document_id])
    return Spread(len(members), sum(len(t) > 1 for t in members.values()),
                  sum(len({lang for lang, _ in t}) > 1 for t in members.values()))
