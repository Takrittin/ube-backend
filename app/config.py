import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ube.db")
if DATABASE_URL.startswith(("postgres://", "postgresql://")):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL.split("://", 1)[1]
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "")
ENABLE_MODEL_TEST_UI = os.getenv("ENABLE_MODEL_TEST_UI", "false").strip().lower() == "true"
ROBOFLOW_API_KEY = os.getenv("ROBOFLOW_API_KEY", "").strip()
ROBOFLOW_MODEL_ID = os.getenv(
    "ROBOFLOW_MODEL_ID", "krittin-wankundee/pallet-detection-zmufh-3-rfdetr-small-t1"
).strip()
ROBOFLOW_CONFIDENCE = float(os.getenv("ROBOFLOW_CONFIDENCE", "0.25"))
ROBOFLOW_TARGET_CLASSES = {
    name.strip().lower().replace("_", "-").replace(" ", "-")
    for name in os.getenv("ROBOFLOW_TARGET_CLASSES", "pallet,pallet-detection,wooden-pallet").split(",")
    if name.strip()
}
# Existing installations retain their previous behavior. Adding a Roboflow key
# selects real detection unless AI_PROVIDER is explicitly configured.
AI_PROVIDER = os.getenv(
    "AI_PROVIDER", "roboflow" if ROBOFLOW_API_KEY else "service" if AI_SERVICE_URL else "mock"
).strip().lower()
AUTO_CHECK_ENABLED = os.getenv("AUTO_CHECK_ENABLED", "true").lower() == "true"
SAM2_MODEL_ID = "facebook/sam2.1-hiera-small"
SAM2_MODEL_DIR = os.getenv("SAM2_MODEL_DIR", str(Path(__file__).resolve().parent.parent / ".model-cache" / "sam2.1-hiera-small"))
SAM2_DEVICE = os.getenv("SAM2_DEVICE", "cpu")
AUTO_CHECK_EXPECTED_LAYERS = int(os.getenv("AUTO_CHECK_EXPECTED_LAYERS", "8"))
AUTO_CHECK_MAX_TILT_DEG = float(os.getenv("AUTO_CHECK_MAX_TILT_DEG", "3"))
AUTO_CHECK_MAX_OFFSET_PERCENT = float(os.getenv("AUTO_CHECK_MAX_OFFSET_PERCENT", "4"))
AUTO_CHECK_CAMERA_ROLL_DEG = float(os.getenv("AUTO_CHECK_CAMERA_ROLL_DEG", "0"))
if not 1 <= AUTO_CHECK_EXPECTED_LAYERS <= 50 or not 0 <= AUTO_CHECK_MAX_TILT_DEG <= 45 or not 0 <= AUTO_CHECK_MAX_OFFSET_PERCENT <= 100 or not -45 <= AUTO_CHECK_CAMERA_ROLL_DEG <= 45:
    raise RuntimeError("Invalid automatic inspection settings")
CORS_ORIGINS = [s.strip() for s in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if s.strip()]

if not JWT_SECRET_KEY or "REPLACE_WITH" in JWT_SECRET_KEY:
    raise RuntimeError("Set a strong JWT_SECRET_KEY in your .env (e.g. openssl rand -hex 32)")

if AI_PROVIDER not in {"mock", "service", "roboflow"}:
    raise RuntimeError("AI_PROVIDER must be mock, service or roboflow")
if not 0 <= ROBOFLOW_CONFIDENCE <= 1:
    raise RuntimeError("ROBOFLOW_CONFIDENCE must be between 0 and 1")
