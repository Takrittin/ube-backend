from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import String, ForeignKey, DateTime, Float, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

def now_utc():
    return datetime.now(timezone.utc)

def new_id():
    return str(uuid4())

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))  # inspector | qa | admin
    is_active: Mapped[bool] = mapped_column(default=True)
    full_name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    email: Mapped[str | None] = mapped_column(String(254), unique=True, index=True, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sku: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    bag_type: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown")

class Lot(Base):
    __tablename__ = "lots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lot_code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    production_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    quantity: Mapped[int] = mapped_column(default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(20), default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class Inspection(Base):
    __tablename__ = "inspections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lot_id: Mapped[str | None] = mapped_column(ForeignKey("lots.id"), index=True, nullable=True)
    inspector_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pallet_code: Mapped[str] = mapped_column(String(100), default="", server_default="")
    material_code: Mapped[str] = mapped_column(String(100), default="", server_default="")
    qr_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class InspectionImage(Base):
    __tablename__ = "inspection_images"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id"), unique=True)
    stored_path: Mapped[str] = mapped_column(String(512))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class AIResult(Base):
    __tablename__ = "ai_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id"), unique=True)
    decision: Mapped[str] = mapped_column(String(20))  # PASS | FAIL | REVIEW
    defect_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    measurements: Mapped[dict] = mapped_column(JSON, default=dict)
    model_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class FinalResult(Base):
    __tablename__ = "final_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id"), unique=True)
    decided_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(20))  # PASS | FAIL
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    defects: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class GeometryMeasurement(Base):
    __tablename__ = "geometry_measurements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id"), unique=True)
    measured_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    annotation: Mapped[dict] = mapped_column(JSON)
    report: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
