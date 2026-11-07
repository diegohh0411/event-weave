"""System One clusterer: one request asks whether any candidate is the same event and which one."""
import math

from weave_core.adapters import AdapterFailure, AdapterSpec
from weave_core.stages import Capability, Stage, UnitKind
from weave_inference.gateway import Inference
from weave_inference.limits import check_limits
from weave_inference.types import Choice, LimitExceeded, Noul, SystemOneRequest

from .base import ClustererBase, ClustererSettings, Decision, MentionContext
from .cards import Card
from .decisions import cached_system_one
from .fields import FIELDS

MIN_CARD_CHARS = 32


def build_request(mention: MentionContext, cards: list[Card]) -> SystemOneRequest:
    lines = [f"Title: {mention.title}"]
    if mention.published_at is not None:
        lines.append(f"Published: {mention.published_at.isoformat()}")
    lines.append(f"Sentence: {mention.sentence}")
    lines += [f"{f.capitalize()}: {mention.fields[f]}" for f in FIELDS if f in mention.fields]
    lines.append("Candidates:")
    lines += [f"c{i}: {card.render()}" for i, card in enumerate(cards, start=1)]
    questions: list = [Noul(id="same", prompt="Does any candidate describe the same real-world event as the mention?")]
    if len(cards) > 1:
        questions.append(Choice(id="which", prompt="Which candidate describes the same real-world event?",
                                options=[f"c{i}" for i in range(1, len(cards) + 1)]))
    return SystemOneRequest(state="\n".join(lines), questions=questions)


class SystemOneClusterer(ClustererBase):
    spec = AdapterSpec(name="system-one", version="1", stage=Stage.cluster, input_kind=UnitKind.mention,
                       settings_model=ClustererSettings, capabilities=frozenset({Capability.system_one}),
                       cacheable=False)

    def _fit(self, deployment, mention, candidates, cards):
        """The request and the candidates it offers. Shrinks cards, then drops the weakest, never exceeds limits."""
        kept = list(candidates)
        vpf, max_chars = self.settings.card_values_per_field, self.settings.card_max_chars
        shrunk, trimmed = False, False
        while True:
            use = (self.rebuild_cards([c.cluster_id for c in kept], vpf, max_chars) if shrunk else cards)
            request = build_request(mention, [use[c.cluster_id] for c in kept])
            try:
                check_limits(deployment, request)
                return request, kept, trimmed, {c.cluster_id: use[c.cluster_id] for c in kept}
            except LimitExceeded as exc:
                if exc.limit != "context_tokens":
                    raise
                if vpf > 1:
                    vpf, shrunk = vpf - 1, True
                elif max_chars // 2 >= MIN_CARD_CHARS:
                    max_chars, shrunk = max_chars // 2, True
                elif len(kept) > 1:
                    kept, trimmed = kept[:-1], True
                else:
                    raise AdapterFailure(f"mention and one candidate exceed the context limit: {exc}",
                                         retryable=False) from exc

    def decide(self, mention, candidates, cards):
        inference = Inference.for_context(self.ctx)
        deployment = inference.deployment(Capability.system_one)
        request, kept, trimmed, shown = self._fit(deployment, mention, candidates, cards)
        answers = {a.question_id: a for a in cached_system_one(inference, self._session, request).answers}
        suffix = "+trimmed" if trimmed else ""
        labels = {c.cluster_id: f"c{i}" for i, c in enumerate(kept, start=1)}
        noul_answer = answers.get("same")
        noul, invalid_noul = 0.0, True  # a missing or null answer is invalid, not 0
        if noul_answer is not None and noul_answer.value is not None:
            try:
                noul = float(noul_answer.value)
                invalid_noul = not (math.isfinite(noul) and 0.0 <= noul <= 1.0)
            except (TypeError, ValueError):
                pass
        if invalid_noul:
            noul = 0.0
        which = answers.get("which")
        probabilities = which.probabilities if which is not None else None

        def result(outcome, cluster_id, reason):
            return Decision(outcome, cluster_id, reason + suffix, noul, probabilities, deployment.id, labels, shown)

        if invalid_noul:
            return result("new", None, "invalid_noul")
        if noul < self.settings.threshold:
            return result("new", None, "noul_below_threshold")
        if len(kept) == 1:
            return result("join", kept[0].cluster_id, "same_event")
        by_label = {label: cid for cid, label in labels.items()}
        chosen = by_label.get(which.value) if which is not None and isinstance(which.value, str) else None
        if chosen is None:
            return result("new", None, "invalid_choice")
        return result("join", chosen, "same_event")
