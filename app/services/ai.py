"""Replace this adapter with the team's YOLO/Roboflow + OpenCV service.

The fallback intentionally returns REVIEW, never a fabricated PASS/FAIL.
"""
import httpx
from app.config import AI_SERVICE_URL

async def analyze_image(image_bytes: bytes, media_type: str) -> dict:
    if not AI_SERVICE_URL:
        return {
            "decision": "REVIEW",
            "defect_type": None,
            "confidence": 0.0,
            "measurements": {},
            "model_version": "mock-no-model-connected",
        }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            AI_SERVICE_URL,
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
