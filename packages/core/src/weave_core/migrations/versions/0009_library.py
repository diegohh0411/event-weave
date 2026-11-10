import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0009_library"
down_revision = "0008_crawls"
branch_labels = None
depends_on = None

metadata = sa.MetaData()
sa.Table("runs", metadata, sa.Column("id", UUID(as_uuid=True), primary_key=True))
sa.Table("documents", metadata, sa.Column("id", sa.Text, primary_key=True))
run_documents = sa.Table("run_documents", metadata,
    sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("position", sa.Integer, primary_key=True),
    sa.Column("document_id", sa.Text, sa.ForeignKey("documents.id"), nullable=False),
    sa.UniqueConstraint("run_id", "document_id"))


def upgrade():
    op.add_column("documents", sa.Column("article_id", sa.Text))
    op.add_column("documents", sa.Column("version", sa.Integer, nullable=False, server_default="1"))
    op.add_column("documents", sa.Column("superseded_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE documents SET article_id = id")
    op.alter_column("documents", "article_id", nullable=False)
    op.create_index("ix_documents_article_id", "documents", ["article_id"])
    op.create_index("ix_documents_latest", "documents", ["article_id"],
                    postgresql_where=sa.text("superseded_at IS NULL"))
    op.add_column("runs", sa.Column("snapshot_at", sa.DateTime(timezone=True)))
    run_documents.create(bind=op.get_bind())
    op.execute("""
        INSERT INTO run_documents (run_id, position, document_id)
        SELECT run_id, row_number() OVER (PARTITION BY run_id ORDER BY order_key, unit_id) - 1, unit_id
        FROM (
            SELECT DISTINCT j.run_id, j.order_key, j.unit_id
            FROM jobs j JOIN documents d ON d.id = j.unit_id
            WHERE j.stage = 'extract' AND j.unit_kind = 'document'
        ) AS legacy_documents
    """)
    op.execute("""UPDATE runs SET snapshot_at = started_at
                  WHERE EXISTS (SELECT 1 FROM run_documents WHERE run_documents.run_id = runs.id)""")


def downgrade():
    run_documents.drop(bind=op.get_bind())
    op.drop_column("runs", "snapshot_at")
    op.drop_index("ix_documents_latest", table_name="documents")
    op.drop_index("ix_documents_article_id", table_name="documents")
    op.drop_column("documents", "superseded_at")
    op.drop_column("documents", "version")
    op.drop_column("documents", "article_id")
