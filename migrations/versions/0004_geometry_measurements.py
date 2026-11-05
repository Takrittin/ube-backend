"""Persist operator inputs and OpenCV reports independently of AI decisions."""
from alembic import op
import sqlalchemy as sa

revision = "0004_geometry_measurements"
down_revision = "0003_human_defects"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("geometry_measurements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("inspection_id", sa.String(36), sa.ForeignKey("inspections.id"), nullable=False, unique=True),
        sa.Column("measured_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("annotation", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE public.geometry_measurements ENABLE ROW LEVEL SECURITY")
        for role in ("anon", "authenticated"):
            if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}).scalar():
                op.execute(f'REVOKE ALL ON public.geometry_measurements FROM "{role}"')


def downgrade():
    raise RuntimeError("Removing saved geometric evidence is intentionally unsupported.")
