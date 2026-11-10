from pathlib import Path
from datetime import date
from typing import Literal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import AIResult, FinalResult, Inspection, InspectionImage, Lot, User, GeometryMeasurement, now_utc
from app.schemas import InspectionCreate, ConfirmRequest, GeometryRequest
from app.security import current_user, require_roles
from app.services.ai import analyze_image
from app.services.roboflow import AIServiceError
from app.services.images import MAX_IMAGE_BYTES, prepare_image
from app.services.geometry import measure, suggest
from app.services.inspection_queries import inspection_page
from app.services.inspection_workflow import (
    InspectionActor, abandon_blocking_upload, check_upload, finish_analysis,
    processing_image, save_upload, utc_timestamp, image_paths,
)

router = APIRouter(prefix="/api/v1/inspections", tags=["inspections"])


def upload_actor(user: User = Depends(require_roles("inspector", "admin")), db: Session = Depends(get_db)):
    actor = InspectionActor(user.id, user.role)
    # Authentication checked the user in this request session. Release its
    # connection before reading/normalizing a photo or awaiting inference.
    db.close()
    return actor


async def analyze_photos(images):
    results = [await analyze_image(blob, media_type) for blob, media_type in images]
    if len(results) == 1:
        return results[0]
    result = dict(results[0])
    decisions = [item["decision"] for item in results]
    result["decision"] = "FAIL" if "FAIL" in decisions else "REVIEW" if "REVIEW" in decisions else "PASS"
    result["confidence"] = min(item["confidence"] for item in results)
    result["measurements"] = {"image_results": results, "scope": "multiple_images"}
    return result


async def process_saved_image(inspection_id, started_at):
    try:
        image = await run_in_threadpool(processing_image, inspection_id, started_at)
        if image is None:
            return
        ai_result = await analyze_photos(image)
        error = None
    except AIServiceError as exc:
        ai_result, error = None, str(exc)
    except Exception:
        ai_result, error = None, "ระบบวิเคราะห์ภาพไม่พร้อมใช้งาน กรุณาลองอีกครั้ง"
    await run_in_threadpool(finish_analysis, inspection_id, started_at, ai_result, error)

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
async def upload_image(inspection_id: str, background_tasks: BackgroundTasks, background: bool = False, file: UploadFile = File(...), files: list[UploadFile] = File(default=[]), actor: InspectionActor = Depends(upload_actor)):
    await run_in_threadpool(check_upload, inspection_id, actor)
    if len(files) > 3:
        raise HTTPException(422, "Use between 1 and 4 photos per inspection")
    additional = []
    for extra in files:
        content = await extra.read(MAX_IMAGE_BYTES + 1)
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Each image must not exceed 10 MB")
        additional.append(await run_in_threadpool(prepare_image, content))
    blob = await file.read(MAX_IMAGE_BYTES + 1)
    if len(blob) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image must not exceed 10 MB")
    blob, extension, media_type = await run_in_threadpool(prepare_image, blob)
    started_at = await run_in_threadpool(save_upload, inspection_id, actor, blob, extension, additional)
    if background:
        background_tasks.add_task(process_saved_image, inspection_id, started_at)
        return JSONResponse({"inspection_id": inspection_id, "status": "PROCESSING"}, status_code=202)
    # Keep compatibility with blocking clients, without holding a transaction
    # during inference. A failed attempt restores the original draft state.
    try:
        ai_result = await analyze_photos([(blob, media_type), *[(content, media) for content, _, media in additional]])
    except AIServiceError as error:
        await run_in_threadpool(abandon_blocking_upload, inspection_id, started_at)
        raise HTTPException(503, str(error)) from None
    except Exception:
        await run_in_threadpool(abandon_blocking_upload, inspection_id, started_at)
        raise HTTPException(503, "AI service unavailable or returned invalid output")
    try:
        finished = await run_in_threadpool(finish_analysis, inspection_id, started_at, ai_result)
    except Exception:
        await run_in_threadpool(abandon_blocking_upload, inspection_id, started_at)
        raise
    if not finished:
        raise HTTPException(409, "Analysis attempt was superseded; reload the inspection")
    return {"inspection_id": inspection_id, "status": "AI_PROCESSED", "ai_result": ai_result}


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
            "final_decision": final.decision, "overridden": overridden,
            "inspection": inspection_data(db, inspection)}

def inspection_data(db: Session, inspection: Inspection):
    row = db.execute(inspection_statement().where(Inspection.id == inspection.id)).one()
    return serialize_inspection(*row)


def inspection_statement():
    # Related records are unique per inspection; preserve optional records with outer joins.
    return (select(Inspection, Lot, AIResult, FinalResult, InspectionImage, User, GeometryMeasurement)
            .outerjoin(Lot, Lot.id == Inspection.lot_id)
            .outerjoin(AIResult, AIResult.inspection_id == Inspection.id)
            .outerjoin(FinalResult, FinalResult.inspection_id == Inspection.id)
            .outerjoin(InspectionImage, InspectionImage.inspection_id == Inspection.id)
            .join(User, User.id == Inspection.inspector_id)
            .outerjoin(GeometryMeasurement, GeometryMeasurement.inspection_id == Inspection.id))


def serialize_inspection(inspection, lot, ai, final, img, inspector, geometry):
    return {
        "inspection_id": inspection.id, "lot_id": lot.id if lot else None, "lot_code": lot.lot_code if lot else None, "status": inspection.status,
        "pallet_code": inspection.pallet_code, "material_code": inspection.material_code, "qr_raw": inspection.qr_raw,
        "inspector_name": inspector.full_name or inspector.username,
        "inspector_id": inspection.inspector_id, "created_at": inspection.created_at,
        "has_image": img is not None,
        "processing_started_at": inspection.processing_started_at,
        "processing_error": inspection.processing_error,
        "image_id": img.id if img else None,
        "images": [{"id": f"{img.id}-{index}", "sequence_no": index}
                   for index, _ in enumerate(image_paths(img.stored_path), 1)] if img else [],
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
def suggest_geometry(inspection_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("inspector", "admin"))):
    image = editable_geometry(db, inspection_id, user)
    ai = db.scalar(select(AIResult).where(AIResult.inspection_id == inspection_id))
    stored_path, measurements = image.stored_path, ai.measurements if ai else {}
    db.rollback()
    return suggest(Path(stored_path).read_bytes(), measurements)


@router.post("/{inspection_id}/geometry")
def save_geometry(inspection_id: str, payload: GeometryRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("inspector", "admin"))):
    image = editable_geometry(db, inspection_id, user)
    stored_path = image.stored_path
    db.rollback()
    annotation = payload.model_dump(mode="json")
    try:
        report = measure(Path(stored_path).read_bytes(), annotation)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    # Confirmation may have happened while CPU work ran; recheck before writing.
    editable_geometry(db, inspection_id, user)
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
    statement = (inspection_statement()
                 .order_by(Inspection.created_at.desc(), Inspection.id).offset(offset).limit(limit))
    return [serialize_inspection(*row) for row in db.execute(statement)]

@router.get("/page")
def paged_inspections(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                      search: str = "", decision: Literal["all", "pass", "fail", "review"] = "all",
                      date_from: date | None = None, date_to: date | None = None,
                      material: str = "", q: str = "",
                      db: Session = Depends(get_db), user: User = Depends(current_user)):
    return inspection_page(db, offset=offset, limit=limit, search=search, decision=decision,
                           date_from=date_from, date_to=date_to, material=material, q=q)


@router.get("/{inspection_id}")
def get_inspection(inspection_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return inspection_data(db, find_inspection(db, inspection_id))

@router.get("/{inspection_id}/image")
def view_image(inspection_id: str, sequence: int = Query(1, ge=1, le=4), db: Session = Depends(get_db), user: User = Depends(current_user)):
    find_inspection(db, inspection_id)
    img = db.scalar(select(InspectionImage).where(InspectionImage.inspection_id == inspection_id))
    if img is None or not Path(img.stored_path).is_file():
        raise HTTPException(404, "Image not found")
    paths = image_paths(img.stored_path)
    if sequence > len(paths):
        raise HTTPException(404, "Image not found")
    return FileResponse(paths[sequence - 1])
