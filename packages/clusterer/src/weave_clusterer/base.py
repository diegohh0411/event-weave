"""Ordered cluster stage: shared flow for every clusterer. Adapters only implement `decide`."""
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field as PField, field_validator, model_validator
from sqlalchemy.orm import Session
from weave_core.adapters import AdapterContext, AdapterFailure, StageResult, UnitRef
from weave_core.config import RunConfig
from weave_core.schema import documents, member_vectors, mentions, runs
from weave_core.stages import Capability, Stage
from weave_inference.gateway import Inference

from .cards import Card, build_card, card_hash
from .fields import FIELDS, field_text
from .retrieval import Candidate, retrieve
from .state import Member, PartitionView
from .vectors import to_literal


class ClustererSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    k: int = PField(ge=1)  # no default: tuned on dev data and recorded in the run config
    weights: dict[str, float]
    threshold: float = PField(ge=0, le=1)
    card_values_per_field: int = PField(default=3, ge=0)
    card_max_chars: int = PField(default=600, gt=0)

    @field_validator("weights")
    @classmethod
    def _weights(cls, w):
        if set(w) != set(FIELDS):
            raise ValueError(f"weights must name exactly {list(FIELDS)}")
        if any(v < 0 for v in w.values()) or sum(w.values()) <= 0:
            raise ValueError("weights must be >= 0 and sum to more than 0")
        return w


@dataclass(frozen=True)
class MentionContext:
    """Everything a decision may use about the mention. Deliberately free of cluster, document and gold ids."""
    mention_id: str
    sentence: str  # the mention's sentence with the trigger wrapped as ⟦trigger⟧
    fields: dict[str, str]  # non-empty enrichment fields only
    title: str
    published_at: datetime | None
    language: str


@dataclass(frozen=True)
class Decision:
    outcome: str  # "join" | "new"
    cluster_id: str | None
    reason: str
    noul: float | None = None
    probabilities: dict | None = None
    deployment: str | None = None
    labels: dict[str, str] | None = None  # cluster id -> the label the model saw, for candidates actually offered


def cluster_id_for(run_id, partition: str, first_mention_id: str) -> str:
    return hashlib.sha256(":".join([str(run_id), partition, first_mention_id]).encode()).hexdigest()[:32]


class ClustererBase:
    spec = None  # set by subclasses

    def __init__(self, settings: ClustererSettings, ctx: AdapterContext):
        self.settings, self.ctx = settings, ctx
        self._embed: tuple[str, int] | None = None
        self._session: Session | None = None  # live during process(), for adapters that need members or a cache
        self._view: PartitionView | None = None

    # -- to implement -------------------------------------------------------
    def decide(self, mention: MentionContext, candidates: list[Candidate], cards: dict[str, Card]) -> Decision:
        raise NotImplementedError

    # -- shared -------------------------------------------------------------
    def rebuild_cards(self, cluster_ids, values_per_field: int, max_chars: int) -> dict[str, Card]:
        """Cards of the given clusters at another size (context trimming). Only valid inside process()."""
        return {cid: build_card(self._view.members(cid), values_per_field, max_chars) for cid in cluster_ids}

    def effective_k(self) -> int:
        k = self.settings.k
        if Capability.system_one in self.spec.capabilities:
            limit = Inference.for_context(self.ctx).deployment(Capability.system_one).limits.max_choice_options
            if limit is not None:
                k = min(k, limit)
        return k

    def embed_scope(self, session: Session) -> tuple[str, int]:
        """The deployment and dimension the run's embed stage used; stored vectors are scoped by them."""
        if self._embed is None:
            cfg = RunConfig.model_validate(session.scalar(sa.select(runs.c.config).where(runs.c.id == self.ctx.run_id)))
            choice = cfg.stages.get(Stage.embed)
            deployment = choice.deployments.get(Capability.embeddings) if choice else None
            dimension = (choice.settings or {}).get("dimension") if choice else None
            if deployment is None or dimension is None:
                raise AdapterFailure("run config has no embed deployment and dimension", retryable=False)
            self._embed = (deployment, int(dimension))
        return self._embed

    def process(self, unit: UnitRef, upstream: Mapping[Stage, dict], partition: str, session: Session) -> StageResult:
        deployment, dimension = self.embed_scope(session)
        run_id = self.ctx.run_id
        enrichment = (upstream.get(Stage.enrich) or {}).get("enrichment") or {}
        mention, member = self._load_mention(session, unit, enrichment, deployment, dimension)
        query = self._query_vectors(session, unit.id, deployment, dimension)
        view = PartitionView(session, run_id, partition, deployment=deployment, dimension=dimension)
        self._session, self._view = session, view
        s = self.settings
        candidates: list[Candidate] = []
        cards: dict[str, Card] = {}
        if not query:
            decision = Decision("new", None, "no_fields")
        elif not view.cluster_ids():
            decision = Decision("new", None, "empty_partition")
        else:
            candidates = retrieve(session, run_id, partition, query, s.weights, self.effective_k(),
                                  deployment=deployment, dimension=dimension)
            if not candidates:
                decision = Decision("new", None, "no_candidates")
            else:
                cards = {c.cluster_id: build_card(view.members(c.cluster_id), s.card_values_per_field, s.card_max_chars)
                         for c in candidates}
                decision = self.decide(mention, candidates, cards)
                if decision.outcome == "join" and decision.cluster_id not in cards:
                    decision = Decision("new", None, "invalid_choice", decision.noul, decision.probabilities,
                                        decision.deployment, decision.labels)
        if decision.outcome == "join":
            cluster_id = decision.cluster_id
            members = view.members(cluster_id) + [member]
        else:
            cluster_id = cluster_id_for(run_id, partition, unit.id)
            members = [member]
        card = build_card(members, s.card_values_per_field, s.card_max_chars)
        created_order = min(m.order_key for m in members)
        rows = {
            "clusters": [{"id": cluster_id, "run_id": run_id, "partition": partition, "card": card.canonical(),
                          "card_hash": card_hash(card), "size": len(members), "created_order": list(created_order)}],
            "cluster_members": [{"run_id": run_id, "mention_id": unit.id, "cluster_id": cluster_id,
                                 "partition": partition, "order_key": list(unit.order_key)}],
            "cluster_decisions": [{
                "run_id": run_id, "mention_id": unit.id, "adapter": self.spec.name,
                "adapter_version": self.spec.version, "deployment": decision.deployment,
                "candidates": [{"cluster_id": c.cluster_id, "score": c.score, "field_scores": c.field_scores,
                                "card_hash": card_hash(cards[c.cluster_id]),
                                "label": (decision.labels or {}).get(c.cluster_id)} for c in candidates],
                "noul": decision.noul, "probabilities": decision.probabilities, "outcome": decision.outcome,
                "reason": decision.reason, "cluster_id": cluster_id}]}
        return StageResult(output={"cluster_id": cluster_id, "outcome": decision.outcome}, emits=None, rows=rows)

    def _query_vectors(self, session, mention_id: str, deployment: str, dimension: int) -> dict[str, str]:
        rows = session.execute(sa.select(member_vectors.c.field, member_vectors.c.vector).where(
            member_vectors.c.mention_id == mention_id, member_vectors.c.kind == "query",
            member_vectors.c.deployment == deployment, member_vectors.c.dimension == dimension)).all()
        by_field = {r.field: to_literal(list(r.vector)) for r in rows}
        return {f: by_field[f] for f in FIELDS if f in by_field}

    def _load_mention(self, session, unit: UnitRef, enrichment: dict, deployment: str,
                      dimension: int) -> tuple[MentionContext, Member]:
        row = session.execute(
            sa.select(mentions.c.start, mentions.c.end, mentions.c.sentence, documents.c.text,
                      documents.c.sentence_offsets, documents.c.language, documents.c.published_at, documents.c.title)
            .select_from(mentions.join(documents, documents.c.id == mentions.c.document_id))
            .where(mentions.c.id == unit.id)).first()
        if row is None:
            raise AdapterFailure(f"mention {unit.id} not found", retryable=False)
        offsets = row.sentence_offsets
        s_start, s_end = offsets[row.sentence] if row.sentence < len(offsets) else (0, len(row.text))
        plain = row.text[s_start:s_end]
        rs, re_ = max(row.start - s_start, 0), min(row.end - s_start, len(plain))
        marked = plain[:rs] + "⟦" + plain[rs:re_] + "⟧" + plain[re_:] if rs < re_ else plain
        fields = {f: t for f in FIELDS if (t := field_text(enrichment, f))}
        vectors = {r.field: list(r.vector) for r in session.execute(
            sa.select(member_vectors.c.field, member_vectors.c.vector).where(
                member_vectors.c.mention_id == unit.id, member_vectors.c.kind == "document",
                member_vectors.c.deployment == deployment, member_vectors.c.dimension == dimension))}
        member = Member(unit.id, tuple(unit.order_key), enrichment, plain, row.language, row.published_at, vectors)
        return MentionContext(unit.id, marked, fields, row.title, row.published_at, row.language), member
