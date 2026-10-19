# UBE Smart Mobile AI Inspection & Traceability — Backend Starter

A runnable learning starter for the UBE Paper/Jumbo Bag inspection capstone. It is intentionally **not** a production-ready factory service.

## What is implemented

- FastAPI REST endpoints and interactive Swagger docs (`/docs`)
- Account login with signed JWT, Argon2 password hashes, and role checks (`inspector`, `qa`, `admin`)
- Product and QR/Lot master data
- Inspection creation, authenticated photo upload, and image retrieval
- An **AI adapter** that returns `REVIEW` if no model is connected (this is NOT a real defect detector)
- Inspector final PASS/FAIL confirmation and a separate database record for AI and human decisions
- Lot history and a simple QA dashboard summary
- Local SQLite quick-start; optional PostgreSQL with Docker Compose

## 1. Prerequisites

- Python 3.12 or newer (3.12 recommended), and optionally Docker Desktop
- Any editor (VS Code is convenient)

## 2. Run locally (without Docker)

From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

- Replace `JWT_SECRET_KEY` with a random value: `openssl rand -hex 32`
- Replace `SEED_ADMIN_PASSWORD` with a strong, private local password
- Leave `DATABASE_URL=sqlite:///./ube.db` for this local learning mode

Then:

```bash
python -m app.seed
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs and http://127.0.0.1:8000/health.

**Important:** Do not commit `.env`, your real company inspection photos, or the SQLite database to Git.

## 3. Run using Docker Compose + PostgreSQL

Set all three in `.env`: `JWT_SECRET_KEY`, `SEED_ADMIN_PASSWORD`, and `POSTGRES_PASSWORD`.
Use a long *alphanumeric* PostgreSQL password for this simple starter Compose example, because it is interpolated into a database URL.

```bash
docker compose up -d --build
docker compose run --rm api python -m app.seed
```

Open http://127.0.0.1:8000/docs.

To stop without destroying the persisted database:

```bash
docker compose down
```

Never run `docker compose down -v` unless you intend to delete the PostgreSQL volume.

## 4. Test complete workflow in Swagger UI (`/docs`)

1. `POST /api/v1/auth/token` — enter your seeded admin username and password in the form (`username`, `password`); copy `access_token`. Use **Authorize** in Swagger with the login credentials (the OAuth2 password flow).
2. `POST /api/v1/products` — e.g. `{"sku":"PAPER-01", "name":"Paper Bag"}`; copy `id`.
3. `POST /api/v1/lots` — e.g. `{"lot_code":"UBE-LOT-001", "product_id":"<copied-product-id>"}`.
4. `GET /api/v1/lots/UBE-LOT-001` — test QR/lot lookup.
5. `POST /api/v1/inspections` — body `{"lot_code":"UBE-LOT-001"}`; copy `inspection_id`.
6. `POST /api/v1/inspections/{inspection_id}/image` — upload a real test JPG, PNG or WEBP photo under 10 MB. **Expect `REVIEW`, `confidence=0.0`, `mock-no-model-connected` until you connect your AI.**
7. `POST /api/v1/inspections/{inspection_id}/confirm` — e.g. `{"decision":"PASS", "notes":"Inspected manually"}`.
8. `GET /api/v1/inspections/{inspection_id}` — confirm AI and human records are separate.
9. `GET /api/v1/lots/UBE-LOT-001/inspections` and `GET /api/v1/dashboard/summary` — check history and totals.

The admin is authorized for all the demo tasks. In a realistic workflow, admins create inspector and QA accounts with `POST /api/v1/users` and each role only accesses allowed operations.

## 5. Project layout

```text
ube-backend-starter/
├── app/
│   ├── main.py               # FastAPI app, CORS, routes
│   ├── config.py             # Environment settings
│   ├── database.py           # SQLAlchemy engine + sessions
│   ├── models.py             # User, Product, Lot, Inspection, image, AI, final tables
│   ├── schemas.py            # Request validation models
│   ├── security.py           # JWT, password hashes, roles
│   ├── seed.py               # Explicit admin-account creation
│   ├── routers/
│   │   ├── auth.py
│   │   ├── lots.py
│   │   ├── inspections.py
│   │   └── dashboard.py
│   └── services/
│       └── ai.py             # Swap mock for local AI inference endpoint
├── tests/smoke_flow.py      # End-to-end lightweight local smoke test
├── compose.yaml             # PostgreSQL + API
├── Dockerfile
├── requirements.txt
└── .env.example
```

## 6. Connect your existing YOLO/Roboflow + OpenCV code

Your existing Google Colab notebook is useful for prototyping. For the application, extract the model-loading and prediction code into a **separate long-running Python inference service** (can be another FastAPI app) that accepts a multipart image and returns:

```json
{
  "decision": "REVIEW",
  "defect_type": null,
  "confidence": 0.0,
  "measurements": {},
  "model_version": "your-model-version"
}
```

This example payload is only an interface example; `REVIEW` is a safe placeholder, **not** a real model prediction.

Configure `AI_SERVICE_URL` in `.env`, e.g. `http://localhost:8001/predict` when running API and inference locally without Docker. If both run as Compose containers, use their Compose service name (e.g. `http://inference:8001/predict`), **not** `localhost`.

`app/services/ai.py` uses that URL if provided; otherwise it deliberately returns manual review. Keep trained-model outputs (bounding boxes, confidence, measurements) as well as versioned **UBE-approved pass/fail tolerances**. Do not silently invent tolerances from sample pictures. Add a versioned backend rule engine to evaluate actual alignment/angle criteria when UBE supplies them.

Using local model weights and OpenCV can avoid per-request Roboflow Cloud inference charges, but requires your own capable machine/server and attention to the model's license, distribution, and hardware needs.

## 7. What must be added before factory production use

- Alembic migration files (this starter uses SQLAlchemy `create_all()` to make initial local development easy)
- Database uniqueness/concurrency design and transaction strategy for simultaneous inspectors
- Formal QA-approved tolerances and versioned rules; robust annotated-image storage
- Image-type hardening, virus scanning as appropriate, lifecycle management, object storage/backups
- HTTPS, secret manager, audit logs, access reviews, safe admin provisioning, rate limits
- Logging, monitoring, alerting, automated tests and backups
- Inference timeouts, retry policy, idempotency, offline/failure workflow
- Connection to real UBE lot/master-data sources and authorization requirements

## 8. Local smoke test

Run outside any existing `.env` database in a disposable SQLite temp folder:

```bash
python -m tests.smoke_flow
```

This tests login, product creation, lot creation, image upload, mock AI, human confirmation, and the dashboard.
