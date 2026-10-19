from io import BytesIO
from pathlib import Path
from uuid import uuid4
from PIL import Image as PILImage, UnidentifiedImageError
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import UPLOAD_DIR
from app.database import get_db
from app.models import AIResult, FinalResult, Inspection, InspectionImage, Lot, User
from app.schemas import InspectionCreate, ConfirmRequest
from app.security import current_user, require_roles
from app.services.ai import analyze_image

router = APIRouter(prefix="/api/v1/inspections", tags=["inspections"])
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG": (".jpg", "image/jpeg"), "PNG": (".png", "image/png"), "WEBP": (".webp", "image/webp")}

def find_inspection(db: Session, inspection_id: str) -> Inspection:
    obj = db.get(Inspection, inspection_id)
    if obj is None:
        raise HTTPException(404, "Inspection not found")
    return obj

@router.post("", status_code=201)
def start_inspection(payload: InspectionCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("inspector", "admin"))):
    lot = db.scalar(select(Lot).where(Lot.lot_code == payload.lot_code))
    if lot is None:
        raise HTTPException(404, "Unknown lot")
    item = Inspection(lot_id=lot.id, inspector_id=user.id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"inspection_id": item.id, "status": item.status}

@router.post("/{inspection_id}/image")
async def upload_image(inspection_id: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_roles("inspector", "admin"))):
    inspection = find_inspection(db, inspection_id)
    if inspection.inspector_id != user.id and user.role != "admin":
        raise HTTPException(403, "Only the inspection owner may upload")
    if inspection.status != "DRAFT":
        raise HTTPException(409, "Image was already submitted")
    blob = await file.read(MAX_IMAGE_BYTES + 1)
    if len(blob) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image must not exceed 10 MB")
    try:
        with PILImage.open(BytesIO(blob)) as image:
            image.verify()
            fmt = image.format
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(415, "Upload a valid JPEG, PNG or WEBP image")
    if fmt not in ALLOWED_FORMATS:
        raise HTTPException(415, "Only JPEG, PNG and WEBP images are supported")
    extension, media_type = ALLOWED_FORMATS[fmt]
    # Inference happens before saving metadata; a failed service call must not create a fake AI result.
    try:
        ai_result = await analyze_image(blob, media_type)
    except Exception:
        raise HTTPException(503, "AI service unavailable or returned invalid output")
    folder = Path(UPLOAD_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    filepath = folder / f"{uuid4().hex}{extension}"
    filepath.write_bytes(blob)
    db.add(InspectionImage(inspection_id=inspection.id, stored_path=str(filepath)))
    db.add(AIResult(inspection_id=inspection.id, **ai_result))
    inspection.status = "AI_PROCESSED"
    try:
        db.commit()
    except Exception:
        db.rollback()
        filepath.unlink(missing_ok=True)
        raise
    return {"inspection_id": inspection.id, "status": inspection.status, "ai_result": ai_result}

@router.post("/{inspection_id}/confirm")
def confirm(inspection_id: str, payload: ConfirmRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("inspector", "admin"))):
    inspection = find_inspection(db, inspection_id)
    if inspection.inspector_id != user.id and user.role != "admin":
        raise HTTPException(403, "Only the inspection owner may confirm")
    if inspection.status != "AI_PROCESSED":
        raise HTTPException(409, "Upload and analyze an image before confirming")
    ai = db.scalar(select(AIResult).where(AIResult.inspection_id == inspection.id))
    overridden = ai.decision in {"PASS", "FAIL"} and ai.decision != payload.decision
    if overridden and not (payload.override_reason or "").strip():
        raise HTTPException(422, "Provide override_reason when changing the AI PASS/FAIL result")
    final = FinalResult(inspection_id=inspection.id, decided_by=user.id, decision=payload.decision,
                        notes=payload.notes, override_reason=payload.override_reason if overridden else None)
    db.add(final)
    inspection.status = "CONFIRMED"
    db.commit()
    return {"inspection_id": inspection.id, "status": inspection.status, "ai_decision": ai.decision,
            "final_decision": final.decision, "overridden": overridden}

@router.get("/{inspection_id}")
def get_inspection(inspection_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    inspection = find_inspection(db, inspection_id)
    lot = db.get(Lot, inspection.lot_id)
    ai = db.scalar(select(AIResult).where(AIResult.inspection_id == inspection.id))
    final = db.scalar(select(FinalResult).where(FinalResult.inspection_id == inspection.id))
    img = db.scalar(select(InspectionImage).where(InspectionImage.inspection_id == inspection.id))
    return {
        "inspection_id": inspection.id, "lot_code": lot.lot_code, "status": inspection.status,
        "inspector_id": inspection.inspector_id, "created_at": inspection.created_at,
        "has_image": img is not None,
        "ai_result": None if ai is None else {"decision": ai.decision, "defect_type": ai.defect_type,
            "confidence": ai.confidence, "measurements": ai.measurements, "model_version": ai.model_version},
        "final_result": None if final is None else {"decision": final.decision, "notes": final.notes,
            "override_reason": final.override_reason, "decided_by": final.decided_by, "confirmed_at": final.confirmed_at},
    }

@router.get("/{inspection_id}/image")
def view_image(inspection_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    find_inspection(db, inspection_id)
    img = db.scalar(select(InspectionImage).where(InspectionImage.inspection_id == inspection_id))
    if img is None or not Path(img.stored_path).is_file():
        raise HTTPException(404, "Image not found")
    return FileResponse(img.stored_path)
