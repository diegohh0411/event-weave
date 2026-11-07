"""Cluster cards: a pure function of the member rows, so rebuilds give the same card and hash."""
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, replace

from .fields import FIELDS, field_text
from .state import Member


@dataclass(frozen=True)
class Card:
    size: int
    languages: tuple[str, ...]
    earliest: str | None
    latest: str | None
    sentence: str
    values: dict[str, tuple[str, ...]]

    def render(self) -> str:
        lines = [f"Sentence: {self.sentence}", f"Mentions: {self.size}"]
        if self.languages:
            lines.append("Languages: " + ", ".join(self.languages))
        if self.earliest:
            lines.append(f"Published: {self.earliest}" + (f" to {self.latest}" if self.latest != self.earliest else ""))
        for f in FIELDS:
            if self.values.get(f):
                lines.append(f"{f}: " + " | ".join(self.values[f]))
        return "\n".join(lines)

    def canonical(self) -> dict:
        return {"size": self.size, "languages": list(self.languages), "earliest": self.earliest,
                "latest": self.latest, "sentence": self.sentence,
                "values": {f: list(v) for f, v in self.values.items()}}


def card_hash(card: Card) -> str:
    return hashlib.sha256(json.dumps(card.canonical(), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def _sort_key(m: Member):
    return (m.order_key, m.mention_id)


def _representative(members: list[Member]) -> Member:
    if len(members) == 1:
        return members[0]

    def mean_similarity(m: Member) -> float:
        sims = []
        for o in members:
            if o is m:
                continue
            shared = [f for f in FIELDS if f in m.vectors and f in o.vectors]
            sims.append(sum(_cosine(m.vectors[f], o.vectors[f]) for f in shared) / len(shared) if shared else 0.0)
        return round(sum(sims) / len(sims), 12)

    return min(members, key=lambda m: (-mean_similarity(m), _sort_key(m)))


def build_card(members: list[Member], values_per_field: int, max_chars: int) -> Card:
    members = sorted(members, key=_sort_key)
    rep = _representative(members)
    values: dict[str, tuple[str, ...]] = {}
    for f in FIELDS:
        counts: Counter = Counter()
        first: dict[str, tuple] = {}
        for m in members:
            t = field_text(m.enrichment, f)
            if t is not None:
                counts[t] += 1
                first.setdefault(t, _sort_key(m))
        if not counts:
            continue
        rep_text = field_text(rep.enrichment, f)
        rest = sorted((t for t in counts if t != rep_text), key=lambda t: (-counts[t], first[t]))
        values[f] = tuple(([rep_text] if rep_text else []) + rest)[:max(values_per_field, 0)]
    dates = [m.published_at for m in members if m.published_at is not None]
    card = Card(size=len(members), languages=tuple(sorted({m.language for m in members})),
                earliest=min(dates).isoformat() if dates else None,
                latest=max(dates).isoformat() if dates else None, sentence=rep.sentence, values=values)
    return _truncate(card, max_chars)


def _truncate(card: Card, max_chars: int) -> Card:
    while len(card.render()) > max_chars:
        longest = max(FIELDS, key=lambda f: (len(card.values.get(f, ())), FIELDS.index(f)))
        if len(card.values.get(longest, ())) > 1:
            card = replace(card, values={**card.values, longest: card.values[longest][:-1]})
            continue
        overflow = len(card.render()) - max_chars
        keep = len(card.sentence) - overflow - 1
        if not card.sentence or keep < 0 and card.sentence == "…":
            break
        sentence = (card.sentence.rstrip("…")[:keep].rstrip() + "…") if keep > 0 else ""
        card = replace(card, sentence=sentence)
        if not sentence:
            break
    return card
