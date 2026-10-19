from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import CORS_ORIGINS
from app.database import Base, engine
from app import models  # noqa: F401: register models with metadata
from app.routers import auth, lots, inspections, dashboard

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Good for the beginner starter only; replace with Alembic before production.
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title="UBE Inspection Backend (Starter)", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
app.include_router(auth.router)
app.include_router(lots.router)
app.include_router(inspections.router)
app.include_router(dashboard.router)

@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "service": "ube-backend"}
