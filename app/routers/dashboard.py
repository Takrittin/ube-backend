from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import FinalResult, Inspection, User, AIResult
from app.security import require_roles

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

@router.get("/summary")
def summary(db: Session = Depends(get_db), user: User = Depends(require_roles("qa", "admin"))):
    confirmed = db.scalar(select(func.count()).select_from(Inspection).where(Inspection.status == "CONFIRMED"))
    passed = db.scalar(select(func.count()).select_from(FinalResult).where(FinalResult.decision == "PASS"))
    failed = db.scalar(select(func.count()).select_from(FinalResult).where(FinalResult.decision == "FAIL"))
    overrides = db.scalar(select(func.count()).select_from(FinalResult).where(FinalResult.override_reason.is_not(None)))
    manual_reviews = db.scalar(select(func.count()).select_from(AIResult).where(AIResult.decision == "REVIEW"))
    return {"confirmed_inspections": confirmed, "passed": passed, "failed": failed,
            "overrides": overrides, "ai_manual_review_count": manual_reviews}
