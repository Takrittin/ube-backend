"""Create the API schema and adopt existing starter databases without deleting data."""
from alembic import op
import sqlalchemy as sa

revision = "0001_inspection_schema"
down_revision = None
branch_labels = None
depends_on = None


def schema():
    # Frozen definitions: do not import changing application models into migrations.
    m = sa.MetaData()
    sa.Table("users", m,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True, index=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("full_name", sa.String(120), nullable=False, server_default=""))
    sa.Table("products", m,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sku", sa.String(80), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("bag_type", sa.String(20), nullable=False, server_default="unknown"))
    sa.Table("lots", m,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lot_code", sa.String(100), nullable=False, unique=True, index=True),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("production_date", sa.String(10)),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    sa.Table("inspections", m,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lot_id", sa.String(36), sa.ForeignKey("lots.id"), nullable=False, index=True),
        sa.Column("inspector_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("pallet_code", sa.String(100), nullable=False, server_default=""),
        sa.Column("material_code", sa.String(100), nullable=False, server_default=""),
        sa.Column("qr_raw", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    sa.Table("inspection_images", m,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("inspection_id", sa.String(36), sa.ForeignKey("inspections.id"), nullable=False, unique=True),
        sa.Column("stored_path", sa.String(512), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False))
    sa.Table("ai_results", m,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("inspection_id", sa.String(36), sa.ForeignKey("inspections.id"), nullable=False, unique=True),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("defect_type", sa.String(100)),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("measurements", sa.JSON(), nullable=False),
        sa.Column("model_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    sa.Table("final_results", m,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("inspection_id", sa.String(36), sa.ForeignKey("inspections.id"), nullable=False, unique=True),
        sa.Column("decided_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("notes", sa.Text()), sa.Column("override_reason", sa.Text()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False))
    return m


def upgrade():
    bind = op.get_bind()
    metadata = schema()
    metadata.create_all(bind, checkfirst=True)
    # create_all intentionally leaves existing tables alone. Add only the new
    # integration columns when adopting a database from the original starter.
    additions = {
        "users": ["full_name"], "products": ["bag_type"],
        "lots": ["production_date", "quantity", "status"],
        "inspections": ["pallet_code", "material_code", "qr_raw"],
    }
    for name, columns in additions.items():
        existing = {c["name"] for c in sa.inspect(bind).get_columns(name)}
        for column in columns:
            if column not in existing:
                source = metadata.tables[name].c[column]
                op.add_column(name, sa.Column(source.name, source.type,
                    nullable=source.nullable, server_default=source.server_default))
    if bind.dialect.name == "postgresql":
        for name in [*metadata.tables, "alembic_version"]:
            op.execute(sa.text(f'ALTER TABLE public."{name}" ENABLE ROW LEVEL SECURITY'))
            # SQLAlchemy connects as the trusted server DB role; browser roles
            # must never bypass FastAPI's authorization or read password hashes.
            for role in ("anon", "authenticated"):
                if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}).scalar():
                    op.execute(sa.text(f'REVOKE ALL ON TABLE public."{name}" FROM "{role}"'))


def downgrade():
    raise RuntimeError("This initial migration is intentionally non-destructive; restore a backup to roll back.")
