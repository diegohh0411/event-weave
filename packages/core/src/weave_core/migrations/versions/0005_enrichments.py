from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()
T = sa.Text
sa.Table("mentions", metadata, sa.Column("id", T, primary_key=True))
enrichments = sa.Table("enrichments", metadata,
    sa.Column("id", T, primary_key=True),
    sa.Column("mention_id", T, sa.ForeignKey("mentions.id"), nullable=False, index=True),
    sa.Column("enricher", T), sa.Column("enricher_version", T), sa.Column("deployment", T),
    *[sa.Column(n, JSONB) for n in ("action", "participants", "location", "time", "discarded")])

revision = "0005_enrichments"
down_revision = "0004_mentions"
branch_labels = None
depends_on = None

def upgrade():
    metadata.create_all(bind=op.get_bind(), tables=[enrichments])

def downgrade():
    metadata.drop_all(bind=op.get_bind(), tables=[enrichments])
