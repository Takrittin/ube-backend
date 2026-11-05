"""Keep inspector-observed defects separate from model predictions."""
from alembic import op
import sqlalchemy as sa

revision = "0003_human_defects"
down_revision = "0002_optional_inspection_lot"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("final_results", sa.Column("defects", sa.JSON(), nullable=False, server_default="[]"))


def downgrade():
    raise RuntimeError("Removing inspector evidence is intentionally unsupported.")
