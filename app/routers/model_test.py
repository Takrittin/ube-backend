"""Opt-in, same-origin model testing without accounts or inspection persistence."""
import base64

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from app import config
from app.services.images import MAX_IMAGE_BYTES, prepare_image
from app.services.roboflow import AIServiceError, configuration_error, detect_pallet


def enabled(request: Request):
    if not config.ENABLE_MODEL_TEST_UI:
        raise HTTPException(404, "Model testing is disabled. Set ENABLE_MODEL_TEST_UI=true for local testing.")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Open the model test UI on the backend's own address.")


router = APIRouter(prefix="/api/v1/model-test", tags=["local model test"], dependencies=[Depends(enabled)])


@router.get("/status")
def status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    error = configuration_error()
    return {
        "provider": "roboflow",
        "configured": error is None,
        "model_id": config.ROBOFLOW_MODEL_ID,
        "target_classes": sorted(config.ROBOFLOW_TARGET_CLASSES),
        "confidence_threshold": config.ROBOFLOW_CONFIDENCE,
        "message": error or "Ready to send a test photo. Model access will be verified on the first detection.",
    }


@router.post("/detect")
async def detect(response: Response, file: UploadFile = File(...)):
    response.headers["Cache-Control"] = "no-store"
    blob = await file.read(MAX_IMAGE_BYTES + 1)
    if len(blob) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image must not exceed 10 MB")
    normalized, _, media_type = await run_in_threadpool(prepare_image, blob)
    try:
        result = await detect_pallet(normalized)
    except AIServiceError as error:
        raise HTTPException(503, str(error)) from None
    except Exception:
        raise HTTPException(503, "Model test failed. Check backend configuration and try again.") from None
    return {
        "ai_result": result,
        "image_data_url": f"data:{media_type};base64,{base64.b64encode(normalized).decode('ascii')}",
    }
