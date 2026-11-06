from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()
T = sa.Text
sa.Table("documents", metadata, sa.Column("id", T, primary_key=True))
mentions = sa.Table("mentions", metadata,
    sa.Column("id", T, primary_key=True),
    sa.Column("document_id", T, sa.ForeignKey("documents.id"), nullable=False, index=True),
    *[sa.Column(n, sa.Integer, nullable=False) for n in ("start", "end", "tok_start", "tok_end", "sentence")],
    *[sa.Column(n, T, nullable=False) for n in ("text", "extractor", "extractor_version")],
    sa.Column("probability", sa.Float))
mention_tags = sa.Table("mention_tags", metadata,
    sa.Column("mention_id", T, sa.ForeignKey("mentions.id"), primary_key=True),
    sa.Column("tagger", T, primary_key=True), sa.Column("tagger_version", T, primary_key=True),
    sa.Column("deployment", T, primary_key=True), sa.Column("significance", sa.Float),
    sa.Column("event_type", T), sa.Column("event_type_probs", JSONB))
NEW = [mentions, mention_tags]

revision = "0004_mentions"
down_revision = "0003_providers"
branch_labels = None
depends_on = None

def upgrade():
    metadata.create_all(bind=op.get_bind(), tables=NEW)

def downgrade():
    metadata.drop_all(bind=op.get_bind(), tables=list(reversed(NEW)))
