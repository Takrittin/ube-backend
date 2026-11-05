from fastapi import APIRouter, Depends

from app.models import User
from app.security import current_user
from app.services.ai import inference_status

router = APIRouter(prefix="/api/v1/ai", tags=["AI configuration"])


@router.get("/status")
def status(user: User = Depends(current_user)):
    """Configuration readiness, not a live inference or model-access check."""
    return inference_status()
