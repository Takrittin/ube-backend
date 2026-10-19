from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Product, Lot, Inspection
from app.schemas import ProductCreate, LotCreate
from app.security import current_user, require_roles

router = APIRouter(prefix="/api/v1", tags=["products and lots"])

@router.post("/products", status_code=201)
def new_product(payload: ProductCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("qa", "admin"))):
    if db.scalar(select(Product).where(Product.sku == payload.sku)):
        raise HTTPException(409, "SKU already exists")
    product = Product(**payload.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return {"id": product.id, "sku": product.sku, "name": product.name}

@router.post("/lots", status_code=201)
def new_lot(payload: LotCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("qa", "admin"))):
    if db.get(Product, payload.product_id) is None:
        raise HTTPException(404, "Unknown product")
    if db.scalar(select(Lot).where(Lot.lot_code == payload.lot_code)):
        raise HTTPException(409, "Lot already exists")
    lot = Lot(**payload.model_dump())
    db.add(lot)
    db.commit()
    db.refresh(lot)
    return {"id": lot.id, "lot_code": lot.lot_code, "product_id": lot.product_id}

@router.get("/lots/{lot_code}")
def get_lot(lot_code: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lot = db.scalar(select(Lot).where(Lot.lot_code == lot_code))
    if lot is None:
        raise HTTPException(404, "Lot not found")
    product = db.get(Product, lot.product_id)
    return {"lot_id": lot.id, "lot_code": lot.lot_code, "product": {"id": product.id, "sku": product.sku, "name": product.name}}

@router.get("/lots/{lot_code}/inspections")
def lot_history(lot_code: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lot = db.scalar(select(Lot).where(Lot.lot_code == lot_code))
    if lot is None:
        raise HTTPException(404, "Lot not found")
    inspections = db.scalars(select(Inspection).where(Inspection.lot_id == lot.id).order_by(Inspection.created_at.desc())).all()
    return [{"id": x.id, "status": x.status, "created_at": x.created_at} for x in inspections]
