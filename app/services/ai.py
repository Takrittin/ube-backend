"""Dispatch to hosted Roboflow, a custom inference service, or explicit mock mode.

Pallet identification and the inspector's final quality decision stay separate.
"""
import httpx
from starlette.concurrency import run_in_threadpool
from app import config
from app.services.roboflow import AIServiceError, configuration_error, detect_pallet


def inference_status() -> dict:
    provider = config.AI_PROVIDER
    error = configuration_error() if provider == "roboflow" else None
    if provider == "service" and not config.AI_SERVICE_URL:
        error = "Set AI_SERVICE_URL on the backend and restart the server."
    from app.services.sam2_runtime import ready
    return {
        "provider": provider,
        "automatic_check": {"enabled": config.AUTO_CHECK_ENABLED and provider == "roboflow",
                            "weights_ready": ready(config.SAM2_MODEL_DIR),
                            "segmentation_model": config.SAM2_MODEL_ID},
        "configured": error is None and provider != "mock",
        "model_id": config.ROBOFLOW_MODEL_ID if provider == "roboflow" else None,
        "target_classes": sorted(config.ROBOFLOW_TARGET_CLASSES) if provider == "roboflow" else [],
        "message": error or (
            "Mock mode. Add ROBOFLOW_API_KEY and set AI_PROVIDER=roboflow in .env, then restart."
            if provider == "mock" else "Configured; live model access has not been verified."
        ),
    }

async def analyze_image(image_bytes: bytes, media_type: str) -> dict:
    if config.AI_PROVIDER == "roboflow":
        output = await detect_pallet(image_bytes)
        if config.AUTO_CHECK_ENABLED:
            from app.services.automatic_check import automatic_check
            report = await run_in_threadpool(automatic_check, image_bytes, output["measurements"])
            output["measurements"]["automatic_check"] = report
            output["measurements"]["scope"] = report["scope"]
            output["measurements"]["note"] = "Provisional visible-stack checks; human confirmation required. Hidden interlocking is not evaluated."
            output["decision"] = report["status"] if report["status"] in {"PASS", "FAIL"} else "REVIEW"
            # Detection confidence is not a calibrated quality confidence.
            output["measurements"]["confidence_scope"] = "object_detection_only"
        return output
    if config.AI_PROVIDER == "mock":
        return {
            "decision": "REVIEW",
            "defect_type": None,
            "confidence": 0.0,
            "measurements": {},
            "model_version": "mock-no-model-connected",
        }
    if not config.AI_SERVICE_URL:
        raise AIServiceError("Set AI_SERVICE_URL on the backend and restart the server.")
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            config.AI_SERVICE_URL,
            files={"file": ("inspection.jpg", image_bytes, media_type)},
        )
        response.raise_for_status()
        output = response.json()
        if output.get("decision") not in {"PASS", "FAIL", "REVIEW"}:
            raise ValueError("AI response decision must be PASS, FAIL or REVIEW")
        confidence = float(output["confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError("AI confidence must be in [0, 1]")
        if not isinstance(output.get("measurements", {}), dict):
            raise ValueError("AI measurements must be an object")
        return {
            "decision": output["decision"],
            "defect_type": output.get("defect_type"),
            "confidence": confidence,
            "measurements": output.get("measurements", {}),
            "model_version": output["model_version"],
        }
