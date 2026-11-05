"""Hosted object detection. Credentials and raw provider errors stay server-side."""
import base64
import re

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app import config


class AIServiceError(Exception):
    """A safe, user-facing inference error without provider credentials."""


class Detection(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    class_name: str = Field(alias="class", min_length=1, max_length=200)
    confidence: float = Field(ge=0, le=1)
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class ImageSize(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class DetectionResponse(BaseModel):
    predictions: list[Detection]
    image: ImageSize


def configuration_error() -> str | None:
    if not config.ROBOFLOW_API_KEY or "REPLACE_WITH" in config.ROBOFLOW_API_KEY:
        return "Add ROBOFLOW_API_KEY to the backend .env file and restart the server."
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config.ROBOFLOW_MODEL_ID):
        return "Set ROBOFLOW_MODEL_ID to the exact model identifier from Roboflow's Deploy tab."
    if len(config.ROBOFLOW_MODEL_ID) > 80:
        return "ROBOFLOW_MODEL_ID exceeds the existing model_version storage limit (80 characters)."
    if not config.ROBOFLOW_TARGET_CLASSES:
        return "Set at least one ROBOFLOW_TARGET_CLASSES label."
    return None


async def detect_pallet(image_bytes: bytes) -> dict:
    if error := configuration_error():
        raise AIServiceError(error)

    # Roboflow's hosted model endpoint accepts a base64 body. A header keeps
    # the key out of query strings, request URLs and ordinary access logs.
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as client:
            response = await client.post(
                f"https://serverless.roboflow.com/{config.ROBOFLOW_MODEL_ID}",
                params={"confidence": config.ROBOFLOW_CONFIDENCE * 100, "format": "json"},
                headers={
                    "Authorization": f"Bearer {config.ROBOFLOW_API_KEY}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                content=base64.b64encode(image_bytes),
            )
    except httpx.TimeoutException:
        raise AIServiceError("Roboflow timed out. No result was saved; you can retry.") from None
    except httpx.RequestError:
        raise AIServiceError("Cannot reach Roboflow. Check the backend's internet connection.") from None

    if response.status_code in {401, 403}:
        raise AIServiceError("Roboflow rejected access. Check the server API key and model permissions.")
    if response.status_code == 404:
        raise AIServiceError("Roboflow model not found. Check ROBOFLOW_MODEL_ID against the Deploy tab.")
    if response.status_code in {402, 429}:
        raise AIServiceError("Roboflow quota or rate limit reached. Check your Roboflow account before retrying.")
    if not response.is_success:
        raise AIServiceError("Roboflow could not process this image. No detection result was saved.")

    try:
        output = response.json()
        if isinstance(output, list) and len(output) == 1:
            output = output[0]
        result = DetectionResponse.model_validate(output)
    except (ValueError, TypeError):
        raise AIServiceError("Roboflow returned an invalid detection response. No result was saved.") from None

    predictions = []
    for detection in result.predictions:
        # Apply the threshold locally too, so persisted evidence matches the
        # advertised filter even if a deployment ignores the request parameter.
        if detection.confidence < config.ROBOFLOW_CONFIDENCE:
            continue
        label = detection.class_name.strip().lower().replace("_", "-").replace(" ", "-")
        predictions.append({
            **detection.model_dump(),
            "is_pallet": label in config.ROBOFLOW_TARGET_CLASSES,
        })
    pallets = [p for p in predictions if p["is_pallet"]]
    return {
        # Object identification is real, but it cannot certify bag/pallet quality.
        "decision": "REVIEW",
        "defect_type": None,
        "confidence": max((p["confidence"] for p in pallets), default=0.0),
        "model_version": config.ROBOFLOW_MODEL_ID,
        "measurements": {
            "provider": "roboflow",
            "scope": "object_detection_only",
            "image": result.image.model_dump(),
            "predictions": predictions,
            "pallet_detected": bool(pallets),
            "pallet_count": len(pallets),
            "target_classes": sorted(config.ROBOFLOW_TARGET_CLASSES),
            "confidence_threshold": config.ROBOFLOW_CONFIDENCE,
            "note": "Real object detection only. Quality tolerances are not evaluated; confirm manually.",
        },
    }
