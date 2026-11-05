from pathlib import Path
from uuid import uuid4
from datetime import timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import UPLOAD_DIR
from app.database import get_db, SessionLocal
from app.models import AIResult, FinalResult, Inspection, InspectionImage, Lot, User, GeometryMeasurement, now_utc
from app.schemas import InspectionCreate, ConfirmRequest, GeometryRequest
from app.security import current_user, require_roles
from app.services.ai import analyze_image
from app.services.roboflow import AIServiceError
from app.services.images import MAX_IMAGE_BYTES, prepare_image
from app.services.geometry import measure, suggest

router = APIRouter(prefix="/api/v1/inspections", tags=["inspections"])


def utc_timestamp(value):
    # SQLite returns naive UTC, while PostgreSQL may return the session timezone.
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(timezone.utc) if value else None


async def process_saved_image(inspection_id, started_at):
    # The task owns its session, independent of the HTTP request session.
    with SessionLocal() as db:
        item = db.get(Inspection, inspection_id)
        if item is None or item.status != "PROCESSING" or utc_timestamp(item.processing_started_at) != utc_timestamp(started_at):
            return
        image = db.scalar(select(InspectionImage).where(InspectionImage.inspection_id == inspection_id))
        stored_path = image.stored_path if image else ""
    # Release the database connection during cloud inference and CPU work.
    try:
        blob = Path(stored_path).read_bytes()
        ai_result = await analyze_image(blob, "image/png" if stored_path.endswith(".png") else "image/jpeg")
        error = None
    except AIServiceError as exc:
        ai_result, error = None, str(exc)
    except Exception:
        ai_result, error = None, "ระบบวิเคราะห์ภาพไม่พร้อมใช้งาน กรุณาลองอีกครั้ง"
    with SessionLocal() as db:
        # Discard a late result from an expired attempt after a retry has claimed the job.
        item = find_inspection(db, inspection_id, lock=True)
        if item.status != "PROCESSING" or utc_timestamp(item.processing_started_at) != utc_timestamp(started_at):
            return
        if ai_result is not None:
            db.add(AIResult(inspection_id=item.id, **ai_result))
            item.status = "AI_PROCESSED"
        else:
            item.status = "AI_FAILED"
        item.processing_error = error
        db.commit()

def find_inspection(db: Session, inspection_id: str, lock: bool = False) -> Inspection:
    obj = db.scalar(select(Inspection).where(Inspection.id == inspection_id).with_for_update()) if lock else db.get(Inspection, inspection_id)
    if obj is None:
        raise HTTPException(404, "Inspection not found")
    return obj

@router.post("", status_code=201)
def start_inspection(payload: InspectionCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("inspector", "admin"))):
    lot = db.scalar(select(Lot).where(Lot.lot_code == payload.lot_code)) if payload.lot_code else None
    if payload.lot_code and lot is None:
        raise HTTPException(404, "Unknown lot")
    if lot and lot.status != "open":
        raise HTTPException(409, "Lot is closed")
    item = Inspection(lot_id=lot.id if lot else None, inspector_id=user.id, pallet_code=payload.pallet_code,
                      material_code=payload.material_code, qr_raw=payload.qr_raw)
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"inspection_id": item.id, "status": item.status}

@router.post("/{inspection_id}/image")
async def upload_image(inspection_id: str, background_tasks: BackgroundTasks, background: bool = False, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_roles("inspector", "admin"))):
    inspection = find_inspection(db, inspection_id, lock=True)
    if inspection.inspector_id != user.id and user.role != "admin":
        raise HTTPException(403, "Only the inspection owner may upload")
    if inspection.status != "DRAFT":
        raise HTTPException(409, "Image was already submitted")
    blob = await file.read(MAX_IMAGE_BYTES + 1)
    if len(blob) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image must not exceed 10 MB")
    blob, extension, media_type = await run_in_threadpool(prepare_image, blob)
    if background:
        folder = Path(UPLOAD_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        filepath = folder / f"{uuid4().hex}{extension}"
        filepath.write_bytes(blob)
        db.add(InspectionImage(inspection_id=inspection.id, stored_path=str(filepath)))
        inspection.status, inspection.processing_started_at, inspection.processing_error = "PROCESSING", now_utc(), None
        try:
            db.commit()
        except Exception:
            db.rollback()
            filepath.unlink(missing_ok=True)
            raise
        background_tasks.add_task(process_saved_image, inspection.id, inspection.processing_started_at)
        return JSONResponse({"inspection_id": inspection.id, "status": "PROCESSING"}, status_code=202)
    # Inference happens before saving metadata; a failed service call must not create a fake AI result.
    try:
        ai_result = await analyze_image(blob, media_type)
    except AIServiceError as error:
        raise HTTPException(503, str(error)) from None
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


@router.post("/{inspection_id}/analyze", status_code=202)
def retry_analysis(inspection_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_roles("inspector", "admin"))):
    item = find_inspection(db, inspection_id, lock=True)
    if item.inspector_id != user.id and user.role != "admin":
        raise HTTPException(403, "Only the inspection owner may retry")
    if item.status == "AI_PROCESSED":
        return {"inspection_id": item.id, "status": item.status}
    expired = item.status == "PROCESSING" and item.processing_started_at and (now_utc() - utc_timestamp(item.processing_started_at)).total_seconds() > 900
    if item.status != "AI_FAILED" and not expired:
        raise HTTPException(409, "Analysis is already running or an image has not been submitted")
    image = db.scalar(select(InspectionImage).where(InspectionImage.inspection_id == item.id))
    if image is None or not Path(image.stored_path).is_file():
        raise HTTPException(404, "Image not found")
    item.status, item.processing_started_at, item.processing_error = "PROCESSING", now_utc(), None
    db.commit()
    background_tasks.add_task(process_saved_image, item.id, item.processing_started_at)
    return {"inspection_id": item.id, "status": item.status}

@router.post("/{inspection_id}/confirm")
def confirm(inspection_id: str, payload: ConfirmRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("inspector", "admin"))):
    inspection = find_inspection(db, inspection_id, lock=True)
    if inspection.inspector_id != user.id and user.role != "admin":
        raise HTTPException(403, "Only the inspection owner may confirm")
    if inspection.status != "AI_PROCESSED":
        raise HTTPException(409, "Upload and analyze an image before confirming")
    ai = db.scalar(select(AIResult).where(AIResult.inspection_id == inspection.id))
    overridden = ai.decision in {"PASS", "FAIL"} and ai.decision != payload.decision
    if overridden and not (payload.override_reason or "").strip():
        raise HTTPException(422, "Provide override_reason when changing the AI PASS/FAIL result")
    final = FinalResult(inspection_id=inspection.id, decided_by=user.id, decision=payload.decision,
                        notes=payload.notes, defects=payload.defects,
                        override_reason=payload.override_reason if overridden else None)
    db.add(final)
    inspection.status = "CONFIRMED"
    db.commit()
    return {"inspection_id": inspection.id, "status": inspection.status, "ai_decision": ai.decision,
            "final_decision": final.decision, "overridden": overridden}

def inspection_data(db: Session, inspection: Inspection):
    lot = db.get(Lot, inspection.lot_id) if inspection.lot_id else None
    ai = db.scalar(select(AIResult).where(AIResult.inspection_id == inspection.id))
    final = db.scalar(select(FinalResult).where(FinalResult.inspection_id == inspection.id))
    img = db.scalar(select(InspectionImage).where(InspectionImage.inspection_id == inspection.id))
    inspector = db.get(User, inspection.inspector_id)
    geometry = db.scalar(select(GeometryMeasurement).where(GeometryMeasurement.inspection_id == inspection.id))
    return {
        "inspection_id": inspection.id, "lot_id": lot.id if lot else None, "lot_code": lot.lot_code if lot else None, "status": inspection.status,
        "pallet_code": inspection.pallet_code, "material_code": inspection.material_code, "qr_raw": inspection.qr_raw,
        "inspector_name": inspector.full_name or inspector.username,
        "inspector_id": inspection.inspector_id, "created_at": inspection.created_at,
        "has_image": img is not None,
        "processing_started_at": inspection.processing_started_at,
        "processing_error": inspection.processing_error,
        "image_id": img.id if img else None,
        "geometry_result": geometry_data(geometry),
        "ai_result": None if ai is None else {"decision": ai.decision, "defect_type": ai.defect_type,
            "confidence": ai.confidence, "measurements": ai.measurements, "model_version": ai.model_version,
            "created_at": ai.created_at},
        "final_result": None if final is None else {"decision": final.decision, "notes": final.notes,
            "defects": final.defects,
            "override_reason": final.override_reason, "decided_by": final.decided_by, "confirmed_at": final.confirmed_at},
    }


def geometry_data(item):
    return None if item is None else {"annotation": item.annotation, "report": item.report,
        "measured_by": item.measured_by, "created_at": item.created_at}


def editable_geometry(db, inspection_id, user):
    item = find_inspection(db, inspection_id, lock=True)
    if item.inspector_id != user.id and user.role != "admin":
        raise HTTPException(403, "Only the inspection owner may measure")
    if item.status != "AI_PROCESSED":
        raise HTTPException(409, "Measure after image analysis and before confirmation")
    image = db.scalar(select(InspectionImage).where(InspectionImage.inspection_id == item.id))
    if image is None or not Path(image.stored_path).is_file():
        raise HTTPException(404, "Image not found")
    return image


@router.post("/{inspection_id}/geometry/suggest")
async def suggest_geometry(inspection_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("inspector", "admin"))):
    image = editable_geometry(db, inspection_id, user)
    ai = db.scalar(select(AIResult).where(AIResult.inspection_id == inspection_id))
    return await run_in_threadpool(suggest, Path(image.stored_path).read_bytes(), ai.measurements if ai else {})


@router.post("/{inspection_id}/geometry")
async def save_geometry(inspection_id: str, payload: GeometryRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("inspector", "admin"))):
    image = editable_geometry(db, inspection_id, user)
    annotation = payload.model_dump(mode="json")
    try:
        report = await run_in_threadpool(measure, Path(image.stored_path).read_bytes(), annotation)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    item = db.scalar(select(GeometryMeasurement).where(GeometryMeasurement.inspection_id == inspection_id))
    if item is None:
        item = GeometryMeasurement(inspection_id=inspection_id)
        db.add(item)
    item.annotation, item.report, item.measured_by, item.created_at = annotation, report, user.id, now_utc()
    db.commit()
    return geometry_data(item)

@router.get("")
def list_inspections(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
                     db: Session = Depends(get_db), user: User = Depends(current_user)):
    items = db.scalars(select(Inspection).order_by(Inspection.created_at.desc(), Inspection.id).offset(offset).limit(limit))
    return [inspection_data(db, item) for item in items]

@router.get("/{inspection_id}")
def get_inspection(inspection_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return inspection_data(db, find_inspection(db, inspection_id))

@router.get("/{inspection_id}/image")
def view_image(inspection_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    find_inspection(db, inspection_id)
    img = db.scalar(select(InspectionImage).where(InspectionImage.inspection_id == inspection_id))
    if img is None or not Path(img.stored_path).is_file():
        raise HTTPException(404, "Image not found")
    return FileResponse(img.stored_path)
