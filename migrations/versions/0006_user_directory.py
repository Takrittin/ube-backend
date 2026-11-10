"""Add user directory metadata without inventing timestamps for legacy accounts."""
from alembic import op
import sqlalchemy as sa

revision = "0006_user_directory"
down_revision = "0005_automatic_processing"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email", sa.String(254), nullable=True))
    op.add_column("users", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def downgrade():
    raise RuntimeError("Restore a backup to remove user directory metadata.")
