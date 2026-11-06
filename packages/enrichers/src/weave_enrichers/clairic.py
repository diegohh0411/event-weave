"""Clairic enricher: one extraction request per batch of mentions, every returned span checked against the document."""
import threading
from collections import OrderedDict

from pydantic import BaseModel, ConfigDict, Field as PField
from weave_core.adapters import AdapterSpec, UnitRef
from weave_core.stages import Capability, Stage, UnitKind
from weave_inference.gateway import Inference
from weave_inference.types import ExtractionRequest, Span

from .keylock import KeyedLocks
from ._shared import Doc, mention_id
from .base import FIELDS, EnricherBase
from .shape import Enrichment, Field, TimeField
from .window import MentionWindow, build_header, build_window

# One sentence per field, shown to the model for review by Diego. `{i}` is the mention's section number.
FIELD_INSTRUCTIONS = {
    "action": "The word or phrase, copied exactly, that states what happened in the event marked ⟦like this⟧ in section [m{i}].",
    "participants": "The people, groups or organisations, copied exactly, that took part in the event marked ⟦like this⟧ in section [m{i}].",
    "location": "The place, copied exactly, where the event marked ⟦like this⟧ in section [m{i}] happened.",
    "time": "The expression, copied exactly, that says when the event marked ⟦like this⟧ in section [m{i}] happened.",
}
CACHE_SIZE = 64


class ClairicSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_sentences: int = PField(default=1, ge=0)


_CACHE: "OrderedDict[tuple, dict[str, tuple[Enrichment, dict[str, int]]]]" = OrderedDict()
_LOCK = threading.RLock()
_KEY_LOCKS = KeyedLocks()
_LOCAL = threading.local()


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _unmark(pos: int, open_at: int, close_at: int) -> int:
    """Position in the marked section -> position in the unmarked window text (the two markers are one char each)."""
    return pos - (open_at < pos) - (close_at < pos)


class ClairicEnricher(EnricherBase):
    spec = AdapterSpec(name="clairic", version="1", stage=Stage.enrich, input_kind=UnitKind.mention,
                       settings_model=ClairicSettings, capabilities=frozenset({Capability.extraction}))

    # -- request -------------------------------------------------------------
    def build_request(self, doc: Doc, windows: list[MentionWindow]) -> tuple[ExtractionRequest, list[tuple[int, int]]]:
        """Header, then one `[m{i}]` section per mention. Also returns each section's (start, end) in the text."""
        header, parts, sections, pos = build_header(doc), [], [], 0
        if header:
            parts.append(header)
            pos = len(header) + 2
        for i, window in enumerate(windows):
            label = f"[m{i}]\n"
            sections.append((pos + len(label), pos + len(label) + len(window.text)))
            parts.append(label + window.text)
            pos += len(label) + len(window.text) + 2
        fields = {f"m{i}_{name}": FIELD_INSTRUCTIONS[name].format(i=i) for i in range(len(windows)) for name in FIELDS}
        return ExtractionRequest(text="\n\n".join(parts), fields=fields), sections

    def map_span(self, doc: Doc, window: MentionWindow, section: tuple[int, int], span: Span) -> tuple[int, int] | None:
        """Document offsets of a span that lies in this section and quotes the document exactly; else None."""
        if not span.text.strip() or not (section[0] <= span.start < span.end <= section[1]):
            return None
        open_at = window.trigger[0] - window.offset
        close_at = open_at + (window.trigger[1] - window.trigger[0]) + 1
        s = window.offset + _unmark(span.start - section[0], open_at, close_at)
        e = window.offset + _unmark(span.end - section[0], open_at, close_at)
        return (s, e) if 0 <= s < e <= len(doc.text) and doc.text[s:e] == span.text else None

    # -- batching ------------------------------------------------------------
    def per_request(self) -> int | None:
        catalog = Inference._catalog
        dep = self.ctx.deployments.get(Capability.extraction)
        max_fields = catalog.deployments[dep].limits.max_fields if catalog and dep in catalog.deployments else None
        return None if max_fields is None else max(1, max_fields // len(FIELDS))

    def run_batch(self, doc: Doc, mentions: list[dict]) -> dict[str, tuple[Enrichment, dict[str, int]]]:
        windows = [build_window(doc, m, self.settings.window_sentences) for m in mentions]
        request, sections = self.build_request(doc, windows)
        response = Inference.for_context(self.ctx).extract(request).value
        out = {}
        for i, mention in enumerate(mentions):
            kept, discarded = {}, {}
            for name in FIELDS:
                found, bad = [], 0
                for span in response.fields.get(f"m{i}_{name}", []):
                    own = self.map_span(doc, windows[i], sections[i], span)
                    if own is None:
                        bad += 1
                    elif own not in [f[:2] for f in found]:
                        found.append((*own, span.confidence))
                kept[name], discarded[name] = found, bad
            out[mention.get("id") or mention_id(mention)] = (self.build_enrichment(doc, kept), discarded)
        return out

    @staticmethod
    def build_enrichment(doc: Doc, kept: dict[str, list]) -> Enrichment:
        def field(name: str, multi: bool, cls=Field):
            found = kept[name]
            if not found:
                return cls()
            if multi:
                found = sorted(found)
                conf = max((c for *_, c in found if c is not None), default=None)
            else:
                best = min(found, key=lambda f: (-(f[2] if f[2] is not None else -1.0), f[0]))
                found, conf = [best], best[2]
            return cls(text="; ".join(doc.text[s:e] for s, e, _ in found), spans=[(s, e) for s, e, _ in found],
                       confidence=conf)

        return Enrichment(action=field("action", False), participants=field("participants", True),
                          location=field("location", False), time=field("time", False, TimeField))

    def enrich_document(self, doc: Doc, mentions: list[dict]) -> dict[str, tuple[Enrichment, dict[str, int]]]:
        """Enrichments of the given mentions of one document, batched by the deployment's field limit and cached."""
        key = (doc.id, self.deployment(), self.settings.model_dump_json())
        ids = [m.get("id") or mention_id(m) for m in mentions]
        with _KEY_LOCKS.hold(key):  # jobs on the same document wait for one another; other documents run concurrently
            with _LOCK:
                cached = dict(_CACHE.get(key, {}))
            todo = [m for m, i in zip(mentions, ids) if i not in cached]
            size = self.per_request() or max(len(todo), 1)
            for i in range(0, len(todo), size):
                cached.update(self.run_batch(doc, todo[i:i + size]))
                with _LOCK:  # store after each batch; a failure leaves earlier batches and no empty entry
                    _CACHE[key] = dict(cached)
                    _CACHE.move_to_end(key)
                    while len(_CACHE) > CACHE_SIZE:
                        _CACHE.popitem(last=False)
            return {i: (cached[i][0].model_copy(deep=True), dict(cached[i][1])) for i in ids}

    # -- stage ---------------------------------------------------------------
    def process(self, unit: UnitRef, upstream):
        _LOCAL.mentions = (upstream.get(Stage.extract) or {}).get("mentions", [])
        return super().process(unit, upstream)

    def enrich(self, doc: Doc, mention: dict, window: MentionWindow) -> tuple[Enrichment, dict[str, int]]:
        mid = mention.get("id") or mention_id(mention)
        siblings = [m for m in getattr(_LOCAL, "mentions", []) if m.get("document_id") == doc.id]
        if mid not in [m.get("id") or mention_id(m) for m in siblings]:
            siblings.append(mention)
        return self.enrich_document(doc, siblings)[mid]
