import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0010_reruns"
down_revision = "0009_library"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("source_run_id", UUID(as_uuid=True)))
    op.add_column("runs", sa.Column("rerun_mode", sa.Text))
    op.create_foreign_key("fk_runs_source_run_id", "runs", "runs", ["source_run_id"], ["id"])
    op.create_check_constraint("ck_runs_rerun_mode", "runs", "rerun_mode IN ('snapshot', 'current')")


def downgrade():
    op.drop_constraint("ck_runs_rerun_mode", "runs", type_="check")
    op.drop_constraint("fk_runs_source_run_id", "runs", type_="foreignkey")
    op.drop_column("runs", "rerun_mode")
    op.drop_column("runs", "source_run_id")
