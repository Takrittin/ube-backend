"""Short database/file operations used by the asynchronous inspection workflow.

Each operation owns its session and runs in a worker thread. No transaction or
ORM object crosses the await that calls an inference provider.
"""
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select, update

from app.config import UPLOAD_DIR
from app.database import SessionLocal
from app.models import AIResult, Inspection, InspectionImage, now_utc


@dataclass(frozen=True)
class InspectionActor:
    id: str
    role: str


def utc_timestamp(value):
    # SQLite returns naive UTC; PostgreSQL may use the session timezone.
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(timezone.utc) if value else None


def check_upload(inspection_id, actor):
    with SessionLocal() as db:
        item = db.get(Inspection, inspection_id)
        if item is None:
            raise HTTPException(404, "Inspection not found")
        if item.inspector_id != actor.id and actor.role != "admin":
            raise HTTPException(403, "Only the inspection owner may upload")
        if item.status != "DRAFT":
            raise HTTPException(409, "Image was already submitted")


def image_paths(stored_path):
    primary = Path(stored_path)
    return [primary, *[path for index in range(2, 5) for extension in (".jpg", ".png", ".webp")
                       if (path := Path(str(primary) + f".{index}{extension}")).is_file()]]


def save_upload(inspection_id, actor, blob, extension, additional=()):
    folder = Path(UPLOAD_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    filepath = folder / f"{uuid4().hex}{extension}"
    started_at = now_utc()
    try:
        filepath.write_bytes(blob)
        for index, (content, extra_extension, _) in enumerate(additional, 2):
            Path(str(filepath) + f".{index}{extra_extension}").write_bytes(content)
        with SessionLocal() as db:
            statement = update(Inspection).where(Inspection.id == inspection_id, Inspection.status == "DRAFT")
            if actor.role != "admin":
                statement = statement.where(Inspection.inspector_id == actor.id)
            claimed = db.execute(statement.values(status="PROCESSING", processing_started_at=started_at,
                                                  processing_error=None)).rowcount
            if claimed != 1:
                db.rollback()
                check_upload(inspection_id, actor)
                raise HTTPException(409, "Image was already submitted")
            db.add(InspectionImage(inspection_id=inspection_id, stored_path=str(filepath)))
            db.commit()
    except BaseException:
        for path in image_paths(filepath):
            path.unlink(missing_ok=True)
        raise
    return started_at


def processing_image(inspection_id, started_at):
    with SessionLocal() as db:
        stored_path = db.scalar(select(InspectionImage.stored_path).join(
            Inspection, Inspection.id == InspectionImage.inspection_id
        ).where(Inspection.id == inspection_id, Inspection.status == "PROCESSING",
                Inspection.processing_started_at == utc_timestamp(started_at)))
    if stored_path is None:
        return None
    return [(path.read_bytes(), "image/" + {".png": "png", ".webp": "webp", ".jpg": "jpeg"}[path.suffix])
            for path in image_paths(stored_path)]


def finish_analysis(inspection_id, started_at, ai_result, error=None):
    with SessionLocal() as db:
        # The conditional update both locks the row and rejects an expired attempt.
        claimed = db.execute(update(Inspection).where(
            Inspection.id == inspection_id, Inspection.status == "PROCESSING",
            Inspection.processing_started_at == utc_timestamp(started_at),
        ).values(status="AI_PROCESSED" if ai_result is not None else "AI_FAILED",
                 processing_error=error)).rowcount
        if claimed != 1:
            db.rollback()
            return False
        if ai_result is not None:
            db.add(AIResult(inspection_id=inspection_id, **ai_result))
        db.commit()
    return True


def abandon_blocking_upload(inspection_id, started_at):
    """Preserve the blocking API's draft/no-image behavior on inference failure."""
    with SessionLocal() as db:
        claimed = db.execute(update(Inspection).where(
            Inspection.id == inspection_id, Inspection.status == "PROCESSING",
            Inspection.processing_started_at == utc_timestamp(started_at),
        ).values(status="DRAFT", processing_started_at=None, processing_error=None)).rowcount
        if claimed != 1:
            db.rollback()
            return
        stored_path = db.scalar(select(InspectionImage.stored_path).where(InspectionImage.inspection_id == inspection_id))
        db.execute(delete(InspectionImage).where(InspectionImage.inspection_id == inspection_id))
        db.commit()
    if stored_path:
        for path in image_paths(stored_path):
            path.unlink(missing_ok=True)
