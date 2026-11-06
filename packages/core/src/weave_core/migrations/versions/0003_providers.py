from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

metadata = sa.MetaData()
T = sa.Text
TZ = sa.DateTime(timezone=True)
sa.Table("documents", metadata, sa.Column("id", T, primary_key=True))
gold_documents = sa.Table("gold_documents", metadata,
    sa.Column("document_id", T, sa.ForeignKey("documents.id"), primary_key=True),
    sa.Column("language", T, nullable=False), sa.Column("topic", T, nullable=False),
    sa.Column("split", T), sa.Column("doc_index", sa.Integer, nullable=False))
gold_mentions = sa.Table("gold_mentions", metadata,
    sa.Column("document_id", T, sa.ForeignKey("documents.id"), primary_key=True),
    sa.Column("mention_id", T, primary_key=True), sa.Column("start", sa.Integer, nullable=False),
    sa.Column("end", sa.Integer, nullable=False), sa.Column("text", T, nullable=False))
gold_pairs = sa.Table("gold_pairs", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    *[sa.Column(n, T, nullable=False) for n in ("language", "kind", "split", "topic", "source_document_id",
                                                 "source_mention_id", "target_document_id", "target_mention_id")],
    sa.Column("label", sa.SmallInteger, nullable=False),
    sa.CheckConstraint("kind in ('within', 'cross')", name="ck_gold_pairs_kind"),
    sa.UniqueConstraint("kind", "source_document_id", "source_mention_id", "target_document_id", "target_mention_id"))
crawled_pages = sa.Table("crawled_pages", metadata,
    sa.Column("canonical_url", T, primary_key=True), sa.Column("url", T, nullable=False),
    sa.Column("outlet", T, nullable=False), sa.Column("fetched_at", TZ, nullable=False),
    sa.Column("status", T, nullable=False), sa.Column("reason", T), sa.Column("raw_html_gz", sa.LargeBinary),
    sa.Column("document_id", T, sa.ForeignKey("documents.id")),
    sa.CheckConstraint("status in ('stored', 'rejected')", name="ck_crawled_pages_status"))
NEW = [gold_documents, gold_mentions, gold_pairs, crawled_pages]

revision = "0003_providers"
down_revision = "0002_inference"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("documents", sa.Column("order_key", ARRAY(sa.BigInteger), nullable=False, server_default="{}"))
    metadata.create_all(bind=op.get_bind(), tables=NEW)

def downgrade():
    metadata.drop_all(bind=op.get_bind(), tables=list(reversed(NEW)))
    op.drop_column("documents", "order_key")
