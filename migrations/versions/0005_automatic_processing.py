"""Track automatic processing without keeping a browser request open."""
from alembic import op
import sqlalchemy as sa

revision = "0005_automatic_processing"
down_revision = "0004_geometry_measurements"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("inspections", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("inspections", sa.Column("processing_error", sa.Text(), nullable=True))


def downgrade():
    raise RuntimeError("Removing processing evidence is intentionally unsupported.")
