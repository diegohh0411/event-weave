from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = sa.MetaData()
T = sa.Text
TZ = sa.DateTime(timezone=True)
sa.Table("runs", metadata, sa.Column("id", UUID(as_uuid=True), primary_key=True))
evaluations = sa.Table("evaluations", metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
    *[sa.Column(n, T, nullable=False) for n in ("config_hash", "gold_hash", "scorer_version", "split")],
    sa.Column("seed", sa.BigInteger, nullable=False), sa.Column("resamples", sa.Integer, nullable=False),
    sa.Column("created_at", TZ, nullable=False), sa.Column("report", JSONB, nullable=False))
sa.Index("ix_evaluations_run_id_created_at", evaluations.c.run_id, evaluations.c.created_at)
evaluation_scores = sa.Table("evaluation_scores", metadata,
    sa.Column("evaluation_id", UUID(as_uuid=True), sa.ForeignKey("evaluations.id"), primary_key=True),
    sa.Column("track", T, primary_key=True), sa.Column("setting", T, primary_key=True),
    sa.Column("language", T, primary_key=True), sa.Column("metric", T, primary_key=True),
    sa.Column("precision", sa.Float), sa.Column("recall", sa.Float), sa.Column("f1", sa.Float, nullable=False),
    sa.Column("f1_low", sa.Float), sa.Column("f1_high", sa.Float),
    sa.CheckConstraint("track in ('paper', 'realistic')", name="ck_evaluation_scores_track"),
    sa.CheckConstraint("setting in ('within', 'cross', 'pooled')", name="ck_evaluation_scores_setting"),
    sa.CheckConstraint("metric in ('muc', 'b3', 'ceafe', 'blanc', 'conll')", name="ck_evaluation_scores_metric"))
NEW = [evaluations, evaluation_scores]

revision = "0007_evaluations"
down_revision = "0006_clusters"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("jobs", sa.Column("cache_hit", sa.Boolean, nullable=False, server_default=sa.false()))
    metadata.create_all(bind=op.get_bind(), tables=NEW)

def downgrade():
    metadata.drop_all(bind=op.get_bind(), tables=list(reversed(NEW)))
    op.drop_column("jobs", "cache_hit")
