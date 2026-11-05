"""Allow inspections without a lot; preserve existing lot associations."""
from alembic import op
import sqlalchemy as sa

revision = "0002_optional_inspection_lot"
down_revision = "0001_inspection_schema"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("inspections") as batch:
        batch.alter_column("lot_id", existing_type=sa.String(36), nullable=True)


def downgrade():
    raise RuntimeError("Cannot require lots again while unassigned inspections may exist.")
