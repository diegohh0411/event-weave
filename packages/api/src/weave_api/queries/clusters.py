from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import distinct_on

from weave_core.schema import cluster_decisions, cluster_members, clusters, documents, mention_tags, mentions


def _facets(run_id: UUID):
    """Per-cluster languages, event types and max significance over its members (run-scoped)."""
    return (sa.select(cluster_members.c.cluster_id,
                      sa.func.array_agg(sa.distinct(documents.c.language)).label("languages"),
                      sa.func.array_remove(sa.func.array_agg(sa.distinct(mention_tags.c.event_type)), None).label("event_types"),
                      sa.func.max(mention_tags.c.significance).label("max_significance"))
            .select_from(cluster_members.join(mentions, mentions.c.id == cluster_members.c.mention_id)
                         .join(documents, documents.c.id == mentions.c.document_id)
                         .outerjoin(mention_tags, mention_tags.c.mention_id == mentions.c.id))
            .where(cluster_members.c.run_id == run_id, cluster_members.c.cluster_id.is_not(None))
            .group_by(cluster_members.c.cluster_id).subquery("facets"))


def search(run_id: UUID, q: str | None, language: str | None, min_size: int | None, max_size: int | None,
           event_type: str | None, min_significance: float | None, sort: str):
    f = _facets(run_id)
    query = (sa.select(clusters.c.id, clusters.c.partition, clusters.c.size, clusters.c.card, clusters.c.card_hash,
                       sa.func.coalesce(f.c.languages, sa.text("'{}'::text[]")).label("languages"),
                       sa.func.coalesce(f.c.event_types, sa.text("'{}'::text[]")).label("event_types"), f.c.max_significance)
             .select_from(clusters.outerjoin(f, f.c.cluster_id == clusters.c.id))
             .where(clusters.c.run_id == run_id))
    if q:
        text_match = (sa.select(1).select_from(cluster_members.join(mentions, mentions.c.id == cluster_members.c.mention_id))
                      .where(cluster_members.c.run_id == run_id, cluster_members.c.cluster_id == clusters.c.id,
                             mentions.c.text.icontains(q, autoescape=True)))
        query = query.where(sa.or_(sa.exists(text_match), sa.cast(clusters.c.card, sa.Text).icontains(q, autoescape=True)))
    if language:
        query = query.where(sa.literal(language) == sa.any_(f.c.languages))
    if event_type:
        query = query.where(sa.literal(event_type) == sa.any_(f.c.event_types))
    if min_size is not None:
        query = query.where(clusters.c.size >= min_size)
    if max_size is not None:
        query = query.where(clusters.c.size <= max_size)
    if min_significance is not None:
        query = query.where(f.c.max_significance >= min_significance)
    order = (clusters.c.created_order.desc(), clusters.c.id) if sort == "recent" else (clusters.c.size.desc(), clusters.c.id)
    return query.order_by(*order)


def cluster_row(conn, run_id: UUID, cluster_id: str):
    return conn.execute(search(run_id, None, None, None, None, None, None, "size").where(clusters.c.id == cluster_id)).one()


def members(run_id: UUID, cluster_id: str):
    tag = (sa.select(mention_tags.c.mention_id, mention_tags.c.significance, mention_tags.c.event_type)
           .ext(distinct_on(mention_tags.c.mention_id))
           .order_by(mention_tags.c.mention_id, mention_tags.c.significance.desc().nulls_last(), mention_tags.c.tagger,
                     mention_tags.c.tagger_version, mention_tags.c.deployment).subquery("tag"))
    s_start = sa.cast(documents.c.sentence_offsets[mentions.c.sentence][0].astext, sa.Integer)
    s_end = sa.cast(documents.c.sentence_offsets[mentions.c.sentence][1].astext, sa.Integer)
    return (sa.select(mentions.c.id.label("mention_id"), mentions.c.document_id, documents.c.title.label("document_title"),
                      documents.c.language, mentions.c.text,
                      sa.func.substr(documents.c.text, s_start + 1, s_end - s_start).label("sentence"),
                      (mentions.c.start - s_start).label("trigger_start"), (mentions.c.end - s_start).label("trigger_end"),
                      tag.c.significance, tag.c.event_type,
                      cluster_decisions.c.adapter, cluster_decisions.c.deployment, cluster_decisions.c.outcome,
                      cluster_decisions.c.reason, cluster_decisions.c.noul, cluster_decisions.c.probabilities,
                      cluster_decisions.c.candidates, cluster_decisions.c.mention_id.label("decided"))
            .select_from(cluster_members.join(mentions, mentions.c.id == cluster_members.c.mention_id)
                         .join(documents, documents.c.id == mentions.c.document_id)
                         .outerjoin(tag, tag.c.mention_id == mentions.c.id)
                         .outerjoin(cluster_decisions, sa.and_(cluster_decisions.c.run_id == cluster_members.c.run_id,
                                                               cluster_decisions.c.mention_id == cluster_members.c.mention_id)))
            .where(cluster_members.c.run_id == run_id, cluster_members.c.cluster_id == cluster_id)
            .order_by(cluster_members.c.order_key, mentions.c.id))
