import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ube.db")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "")
CORS_ORIGINS = [s.strip() for s in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if s.strip()]

if not JWT_SECRET_KEY or "REPLACE_WITH" in JWT_SECRET_KEY:
    raise RuntimeError("Set a strong JWT_SECRET_KEY in your .env (e.g. openssl rand -hex 32)")
