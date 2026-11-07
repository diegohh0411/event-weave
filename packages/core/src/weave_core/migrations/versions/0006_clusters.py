from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from weave_core.vector import Vector

metadata = sa.MetaData()
T = sa.Text
TZ = sa.DateTime(timezone=True)
sa.Table("runs", metadata, sa.Column("id", UUID(as_uuid=True), primary_key=True))
sa.Table("mentions", metadata, sa.Column("id", T, primary_key=True))
sa.Table("enrichments", metadata, sa.Column("id", T, primary_key=True))
member_vectors = sa.Table("member_vectors", metadata,
    sa.Column("id", T, primary_key=True),
    sa.Column("mention_id", T, sa.ForeignKey("mentions.id"), nullable=False),
    sa.Column("enrichment_id", T, sa.ForeignKey("enrichments.id"), nullable=False),
    sa.Column("field", T, nullable=False), sa.Column("kind", T, nullable=False),
    sa.Column("deployment", T, nullable=False), sa.Column("dimension", sa.Integer, nullable=False),
    sa.Column("vector", Vector(), nullable=False),
    sa.CheckConstraint("kind in ('document', 'query')", name="ck_member_vectors_kind"))
sa.Index("ix_member_vectors_mention_id_field_kind", member_vectors.c.mention_id, member_vectors.c.field, member_vectors.c.kind)
clusters = sa.Table("clusters", metadata,
    sa.Column("id", T, primary_key=True), sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id")),
    sa.Column("partition", T), sa.Column("card", JSONB), sa.Column("card_hash", T), sa.Column("size", sa.Integer),
    sa.Column("created_order", ARRAY(sa.BigInteger)))
sa.Index("ix_clusters_run_id_partition", clusters.c.run_id, clusters.c.partition)
cluster_members = sa.Table("cluster_members", metadata,
    sa.Column("run_id", UUID(as_uuid=True), primary_key=True), sa.Column("mention_id", T, primary_key=True),
    sa.Column("cluster_id", T, sa.ForeignKey("clusters.id")), sa.Column("partition", T),
    sa.Column("order_key", ARRAY(sa.BigInteger)))
cluster_decisions = sa.Table("cluster_decisions", metadata,
    sa.Column("run_id", UUID(as_uuid=True), primary_key=True), sa.Column("mention_id", T, primary_key=True),
    sa.Column("adapter", T), sa.Column("adapter_version", T), sa.Column("deployment", T),
    sa.Column("candidates", JSONB), sa.Column("noul", sa.Float), sa.Column("probabilities", JSONB),
    sa.Column("outcome", T), sa.Column("reason", T), sa.Column("cluster_id", T),
    sa.CheckConstraint("outcome in ('join', 'new')", name="ck_cluster_decisions_outcome"))
decision_cache = sa.Table("decision_cache", metadata,
    sa.Column("key", T, primary_key=True), sa.Column("deployment", T), sa.Column("response", JSONB),
    sa.Column("created_at", TZ, server_default=sa.func.now()))
NEW = [member_vectors, clusters, cluster_members, cluster_decisions, decision_cache]

revision = "0006_clusters"
down_revision = "0005_enrichments"
branch_labels = None
depends_on = None

def upgrade():
    metadata.create_all(bind=op.get_bind(), tables=NEW)

def downgrade():
    metadata.drop_all(bind=op.get_bind(), tables=list(reversed(NEW)))
