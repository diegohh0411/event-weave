"""System One enricher: Stanza proposes candidates in code, yes/no and choice questions pick among them.
No model call for the action; nothing a model says is ever turned into text, so only Stanza misalignment is discarded."""
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field as PField
from weave_core.adapters import AdapterFailure, AdapterSpec
from weave_core.stages import Capability, Stage, UnitKind
from weave_inference.gateway import Inference
from weave_inference.types import Choice, LimitExceeded, Noul, SystemOneRequest

from ._shared import Doc, HttpStanzaClient, Sentence, StanzaClient, check_language
from .base import EnricherBase
from .shape import Enrichment, Field, TimeField
from .window import MentionWindow

Span = tuple[int, int]

# Stanza NER labels per candidate kind; any other label maps to nothing.
PARTICIPANT_ENTITIES = {"PERSON", "PER", "ORG", "NORP"}
LOCATION_ENTITIES = {"LOC", "GPE", "FAC"}
TIME_ENTITIES = {"DATE", "TIME"}

PARTICIPANT_RELS = {"nsubj", "nsubj:pass", "obj", "iobj", "obl", "obl:agent"}
TIME_RELS = {"obl:tmod", "nmod:tmod"}
PHRASE_CHILDREN = {"det", "amod", "compound", "flat", "nmod", "nummod"}  # nummod: "two men" is one phrase
ACTION_RELS = {"aux:pass", "compound:prt", "xcomp"}

# Case markers (lower-case) that make an `obl` phrase a location, per language. A head whose feats say
# Case=Loc (Turkish suffixes, Hindi/Ukrainian cases) counts too.
LOCATIVE_MARKERS = {
    "en": {"in", "at", "near", "inside", "outside", "around", "across", "within", "beside", "behind", "under", "over"},
    "es": {"en", "cerca", "dentro", "fuera", "junto", "alrededor", "ante", "bajo", "sobre", "entre"},
    "hi": {"में", "पर", "पास", "के"},
    "tr": {"içinde", "yakınında", "civarında", "dışında"},
    "uk": {"в", "у", "на", "біля", "поблизу", "коло", "під", "над", "серед", "навколо"},
}

PARSE_CACHE_SIZE = 16
_PARSES: "OrderedDict[tuple, list[Sentence]]" = OrderedDict()
_PARSE_LOCK = threading.Lock()  # bookkeeping only: never held across a parse
_KEY_LOCKS: dict[tuple, threading.Lock] = {}


def clear_cache() -> None:
    with _PARSE_LOCK:
        _PARSES.clear()
        _KEY_LOCKS.clear()


@dataclass
class Proposals:
    participants: list[Span] = field(default_factory=list)
    locations: list[Span] = field(default_factory=list)
    times: list[Span] = field(default_factory=list)
    action_span: Span | None = None
    misaligned: int = 0


def _add(spans: list[Span], span: Span) -> None:
    if span not in spans:
        spans.append(span)


def _phrase(words, head: int) -> Span:
    """Head plus its det/amod/compound/flat/nmod/nummod children, only as far as they stay contiguous."""
    attached = {head} | {i for i, w in enumerate(words)
                         if w.head == head + 1 and w.deprel.split(":")[0] in PHRASE_CHILDREN
                         and w.deprel not in TIME_RELS and w.deprel != "compound:prt"}
    lo = hi = head
    while lo - 1 in attached:
        lo -= 1
    while hi + 1 in attached:
        hi += 1
    return words[lo].start, words[hi].end


def _run(words, trigger: int, members: set[int]) -> Span:
    lo = hi = trigger
    while lo - 1 in members:
        lo -= 1
    while hi + 1 in members:
        hi += 1
    return words[lo].start, words[hi].end


def find_trigger(sentences: list[Sentence], trigger: Span) -> tuple[int, int] | None:
    """(sentence, word) of the trigger's head word: the overlapping word whose head lies outside the trigger."""
    for si, sentence in enumerate(sentences):
        hit = [i for i, w in enumerate(sentence.words) if w.start < trigger[1] and w.end > trigger[0]]
        if hit:
            outer = [i for i in hit if sentence.words[i].head - 1 not in hit]
            return si, (outer or hit)[0]
    return None


def proposals(text: str, sentences: list[Sentence], trigger_idx: tuple[int, int] | None, window: Span | None = None,
              lang: str = "en") -> Proposals:
    """Candidates from the window's Stanza sentences. `trigger_idx` is (sentence, word) in `sentences`; entities are
    kept when inside `window` (document offsets, default everything)."""
    out = Proposals()
    for sentence in sentences:
        for e in sentence.entities:
            if window and not (window[0] <= e.start and e.end <= window[1]):
                continue
            kinds = [(PARTICIPANT_ENTITIES, out.participants), (LOCATION_ENTITIES, out.locations), (TIME_ENTITIES, out.times)]
            targets = [spans for labels, spans in kinds if e.type in labels]
            if not targets:
                continue
            if text[e.start:e.end] != e.text or e.start >= e.end:
                out.misaligned += 1
                continue
            for spans in targets:
                _add(spans, (e.start, e.end))
    if trigger_idx is None:
        return out
    si, ti = trigger_idx
    words = sentences[si].words
    lang_markers = LOCATIVE_MARKERS.get(lang, set())
    action = {ti}
    for i, w in enumerate(words):
        if w.head != ti + 1 or i == ti:
            continue
        if w.deprel in ACTION_RELS:
            action.add(i)
        if w.deprel in TIME_RELS:
            _add(out.times, _phrase(words, i))
        elif w.deprel == "obl":
            cases = [c for c in words if c.head == i + 1 and c.deprel.split(":")[0] == "case"]
            if "Case=Loc" in (w.feats or "") or any(c.text.lower() in lang_markers or (c.lemma or "").lower() in lang_markers for c in cases):
                _add(out.locations, _phrase(words, i))
            else:
                _add(out.participants, _phrase(words, i))
        elif w.deprel in PARTICIPANT_RELS and w.upos != "PUNCT":
            _add(out.participants, _phrase(words, i))
    out.action_span = _run(words, ti, action)
    return out



class SystemOneSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    participant_threshold: float = PField(ge=0, le=1)  # chosen on English dev; frozen in the run config
    stated_threshold: float = PField(ge=0, le=1)
    window_sentences: int = PField(default=1, ge=0)
    stanza_url: str | None = None


class SystemOneEnricher(EnricherBase):
    spec = AdapterSpec(name="system-one", version="1", stage=Stage.enrich, input_kind=UnitKind.mention,
                       settings_model=SystemOneSettings, capabilities=frozenset({Capability.system_one}))

    def client(self) -> StanzaClient:
        return HttpStanzaClient(self.settings.stanza_url)

    def parse(self, doc: Doc) -> list[Sentence]:
        check_language(doc.language)
        key = (doc.id, doc.content_hash, self.settings.stanza_url)
        with _PARSE_LOCK:
            key_lock = _KEY_LOCKS.setdefault(key, threading.Lock())
        with key_lock:  # mentions of one document wait for a single parse; other documents run concurrently
            with _PARSE_LOCK:
                if key in _PARSES:
                    _PARSES.move_to_end(key)
                    return _PARSES[key]
            sentences = self.client().parse(doc.text, doc.language, ner=True)
            with _PARSE_LOCK:
                _PARSES[key] = sentences
                while len(_PARSES) > PARSE_CACHE_SIZE:
                    old, _ = _PARSES.popitem(last=False)
                    _KEY_LOCKS.pop(old, None)
        return sentences

    # -- deployment ----------------------------------------------------------
    def deployment_info(self):
        catalog = Inference._catalog
        dep = self.ctx.deployments.get(Capability.system_one)
        return catalog.deployments[dep] if catalog and dep in catalog.deployments else None

    # -- questions -----------------------------------------------------------
    def send(self, state: str, questions: list) -> dict[str, tuple]:
        """Answers by question id (value, probabilities). One request when it fits; halved on LimitExceeded."""
        if not questions:
            return {}
        try:
            result = Inference.for_context(self.ctx).system_one(SystemOneRequest(state=state, questions=questions))
        except LimitExceeded as exc:
            if len(questions) == 1:
                raise AdapterFailure(f"one question exceeds the deployment's limits: {exc}", retryable=False) from exc
            half = len(questions) // 2
            return {**self.send(state, questions[:half]), **self.send(state, questions[half:])}
        return {a.question_id: (a.value, a.probabilities) for a in result.value.answers}

    def choose(self, state: str, kinds: dict[str, tuple[str, list[str]]], noul: list) -> tuple[dict, dict[str, tuple]]:
        """Run the Noul questions together with every kind's Choice rounds; return all Noul answers and, per kind,
        (winner, probability). A kind with one option needs no Choice. Rounds of at most max_choice_options."""
        info = self.deployment_info()  # joint deployments need no special case: a job is one mention, so one request
        cap = info.limits.max_choice_options if info else None
        levels = {kind: options for kind, (_, options) in kinds.items() if len(options) > 1}
        winners: dict[str, tuple] = {}
        rounds = dict.fromkeys(levels, 0)
        answers: dict[str, tuple] = {}
        pending_noul = noul
        while levels or pending_noul:
            batch, shape = list(pending_noul), {}
            for kind, options in levels.items():
                if cap == 1:
                    raise AdapterFailure("deployment allows 1 choice option: a Choice needs at least 2", retryable=False)
                size = cap or len(options)
                count = -(-len(options) // size)  # balanced chunks, so none is left with a single option
                bounds = [round(i * len(options) / count) for i in range(count + 1)]
                chunks = [options[bounds[i]:bounds[i + 1]] for i in range(count)]
                final = len(chunks) == 1
                shape[kind] = []
                for j, chunk in enumerate(chunks):
                    qid = kind if final else f"{kind}_r{rounds[kind]}_{j}"
                    shape[kind].append((qid, chunk))
                    batch.append(Choice(id=qid, prompt=kinds[kind][0], options=chunk))
            got = self.send(state, batch)  # one request; a joint deployment is never split except on LimitExceeded
            answers.update(got)
            pending_noul = []
            rounds = {k: n + 1 for k, n in rounds.items()}
            for kind in list(levels):
                round_winners = []
                for qid, chunk in shape[kind]:
                    value, probs = got.get(qid, (None, None))
                    if value in chunk:
                        round_winners.append((value, (probs or {}).get(value)))
                if len(shape[kind]) == 1:  # that was the final choice
                    winners[kind] = round_winners[0] if round_winners else (None, None)
                    del levels[kind]
                elif round_winners:
                    levels[kind] = [w for w, _ in round_winners]
                    if len(levels[kind]) == 1:
                        winners[kind] = round_winners[0]
                        del levels[kind]
                else:
                    winners[kind] = (None, None)
                    del levels[kind]
        return answers, winners

    # -- stage ---------------------------------------------------------------
    def enrich(self, doc: Doc, mention: dict, window: MentionWindow) -> tuple[Enrichment, dict[str, int]]:
        sentences = self.parse(doc)
        w_start, w_end = window.offset, window.offset + len(window.text) - 2
        inside = [s for s in sentences if s.words and s.words[0].start < w_end and s.words[-1].end > w_start]
        props = proposals(doc.text, inside, find_trigger(inside, window.trigger), (w_start, w_end), doc.language)
        anchor = window.trigger[0]

        def nearest_by_text(spans: list[Span]) -> list[Span]:
            best: dict[str, Span] = {}
            for s in spans:
                t = doc.text[s[0]:s[1]]
                if t not in best or abs(s[0] - anchor) < abs(best[t][0] - anchor):
                    best[t] = s
            return list(best.values())

        participants = sorted(props.participants)
        options = {"loc": nearest_by_text(props.locations), "time": nearest_by_text(props.times)}
        state = "\n\n".join(p for p in (window.header, window.text) if p)
        noul = [Noul(id=f"p{i}", prompt=f"Is “{doc.text[s:e]}” a participant in the marked event?")
                for i, (s, e) in enumerate(participants)]
        prompts = {"loc": ("Does the text state where the marked event happened?", "Where did the marked event happen?"),
                   "time": ("Does the text state when the marked event happened?", "When did the marked event happen?")}
        for kind, spans in options.items():
            if spans:
                noul.append(Noul(id=f"{kind}_stated", prompt=prompts[kind][0]))
        kinds = {k: (prompts[k][1], [doc.text[s:e] for s, e in spans]) for k, spans in options.items()
                 if len(spans) > 1}
        answers, winners = self.choose(state, kinds, noul)

        def prob(qid: str) -> float | None:
            value = answers.get(qid, (None, None))[0]
            return None if value is None else float(value)

        scored = [(s, prob(f"p{i}")) for i, s in enumerate(participants)]
        kept = [(s, p) for s, p in scored if p is not None and p >= self.settings.participant_threshold]
        part = Field()
        if kept:
            part = Field(text="; ".join(doc.text[s:e] for (s, e), _ in kept), spans=[s for s, _ in kept],
                         confidence=max(p for _, p in kept))

        def stated(kind: str, cls):
            spans = options[kind]
            p = prob(f"{kind}_stated")
            if not spans or p is None or p < self.settings.stated_threshold:
                return cls()
            if len(spans) == 1:
                return cls(text=doc.text[spans[0][0]:spans[0][1]], spans=[spans[0]], confidence=None)
            value, conf = winners.get(kind, (None, None))
            chosen = next((s for s in spans if doc.text[s[0]:s[1]] == value), None)
            if chosen is None:
                return cls()
            return cls(text=value, spans=[chosen], confidence=conf)

        s, e = props.action_span or window.trigger
        s, e = min(s, window.trigger[0]), max(e, window.trigger[1])
        action = Field(text=doc.text[s:e], spans=[(s, e)], confidence=None)
        enrichment = Enrichment(action=action, participants=part, location=stated("loc", Field),
                                time=stated("time", TimeField))
        return enrichment, {"misaligned": props.misaligned}

