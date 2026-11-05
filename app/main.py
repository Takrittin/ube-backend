from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect
from app.config import CORS_ORIGINS, DATABASE_URL
from app.database import Base, engine
from app import models  # noqa: F401: register models with metadata
from app.routers import auth, lots, inspections, dashboard, ai, model_test

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Keep fresh offline demos/tests convenient; cloud schema changes are explicit migrations.
    if DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name) or not set(table.c.keys()).issubset(
            {column["name"] for column in inspector.get_columns(table.name)}
        ):
            raise RuntimeError("Database schema is outdated. Run: python -m app.migrate")
    yield

app = FastAPI(title="UBE Inspection Backend (Starter)", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])
app.include_router(auth.router)
app.include_router(lots.router)
app.include_router(inspections.router)
app.include_router(dashboard.router)
app.include_router(ai.router)
app.include_router(model_test.router)
app.mount("/test-ui", StaticFiles(directory=Path(__file__).resolve().parent.parent / "test-ui", html=True), name="test-ui")

@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "service": "ube-backend"}
