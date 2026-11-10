"""Record the settings used to build a backfill frontier."""
from alembic import op
import sqlalchemy as sa

revision = '0013_backfill_frontier_settings'
down_revision = '0012_backfill'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('sites', sa.Column('backfill_frontier_mode', sa.Text()))
    op.add_column('sites', sa.Column('backfill_frontier_from', sa.Date()))
    op.execute("UPDATE sites SET backfill_frontier_mode = backfill_mode, backfill_frontier_from = backfill_from WHERE backfill_started_at IS NOT NULL")


def downgrade():
    op.drop_column('sites', 'backfill_frontier_from')
    op.drop_column('sites', 'backfill_frontier_mode')
