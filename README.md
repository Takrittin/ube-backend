# UBE Smart Mobile AI Inspection & Traceability — Backend Starter

A runnable learning starter for the UBE Paper/Jumbo Bag inspection capstone. It is intentionally **not** a production-ready factory service.

The sibling Next.js frontend now connects to these APIs. For Supabase PostgreSQL, schema migrations, frontend setup and the connected workflow, see [SUPABASE_SETUP.md](SUPABASE_SETUP.md). Authentication remains FastAPI JWT; images remain in backend uploads.

## What is implemented

- FastAPI REST endpoints and interactive Swagger docs (`/docs`)
- Account login with signed JWT, Argon2 password hashes, and role checks (`inspector`, `qa`, `admin`)
- Product and QR/Lot master data
- Inspection creation, authenticated photo upload, and image retrieval
- Real Roboflow Cloud pallet detection, with stored boxes, class labels and confidence
- A no-login model test UI at `/test-ui/` for photo upload, boxes and confidence
- Separate mock and external-service modes; object detection alone always requires manual quality review
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

Open **http://127.0.0.1:8000/test-ui/** for the test interface. It is served by
FastAPI itself; no Node.js, frontend install, second server or additional CORS
configuration is needed. The model test UI needs no account or lot. For local
Python runs, set `ENABLE_MODEL_TEST_UI=true`; Compose enables it automatically
on its localhost-only port. Account seeding is only needed for the protected
inspection workflow in Swagger.

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
6. `POST /api/v1/inspections/{inspection_id}/image` — upload a test JPG, PNG or WEBP photo under 10 MiB and 25 megapixels. Real Roboflow mode returns detections inside `ai_result.measurements`; its quality decision is still `REVIEW`. Explicit mock mode returns confidence `0.0` and `mock-no-model-connected`.
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
│       ├── ai.py             # Provider dispatch and safe configuration status
│       └── roboflow.py       # Hosted pallet detection and response validation
├── test-ui/                  # Plain HTML/CSS/JS test workspace
├── tests/smoke_flow.py      # End-to-end lightweight local smoke test
├── compose.yaml             # PostgreSQL + API
├── Dockerfile
├── requirements.txt
└── .env.example
```

## 6. Test real Roboflow pallet detection

Set these values in your local `.env` file (never commit your private key):

```dotenv
AI_PROVIDER=roboflow
ROBOFLOW_API_KEY=YOUR_PRIVATE_ROBOFLOW_KEY
ROBOFLOW_MODEL_ID=krittin-wankundee/pallet-detection-zmufh-4-rfdetr-small-t1
ROBOFLOW_CONFIDENCE=0.25
ROBOFLOW_TARGET_CLASSES=pallet,pallet-detection,wooden-pallet
```

Restart the backend after changing settings, then open `/test-ui/`:

1. Choose a test photo. No username, password, lot or product is required.
2. Select **Run real detection**. One request is sent to Roboflow Cloud and may
   consume inference credits; there are no automatic retries.
3. Inspect class labels, confidence and bounding boxes. Green boxes match
   `ROBOFLOW_TARGET_CLASSES`; blue boxes show other labels. A successful response
   with no pallet labels means the model did not identify a target above the
   configured threshold. It does not mean the request failed.

`GET /api/v1/model-test/status` and `POST /api/v1/model-test/detect` are
no-login, opt-in endpoints for this local sandbox. They call Roboflow directly,
regardless of the provider used by the protected inspection workflow. They do
not write images, lots, inspections or decisions to storage. The returned image
is orientation-normalized so its detection overlay matches the inferred pixels.
The API key remains server-side. Model credentials/access are verified only when
you intentionally run detection, not when the page loads.

The sandbox defaults to disabled (`ENABLE_MODEL_TEST_UI=false`). Compose enables
it for the existing `127.0.0.1:8000` binding. Leave the flag off on public
installations. Requests from other browser origins are rejected. The existing
login, inspection, lot and account APIs retain their authentication and records.

**Object detection does not certify quality.** This test does not evaluate
alignment, tilt, wrapping, layer counts or product-quality Pass/Fail tolerances.
Confirm the exact model ID and labels using Roboflow's Deploy tab if needed.

For full inspection API use, provider selection is unchanged: an explicit
`AI_PROVIDER` takes precedence; otherwise a Roboflow key selects Roboflow, a
custom `AI_SERVICE_URL` selects the external service, and no AI configuration
selects mock mode. The separate `/api/v1/ai/status` remains authenticated.

## 7. Automatic visible-stack inspection

The protected inspection flow now runs **Roboflow layer/stack detection → local SAM 2.1 segmentation → OpenCV/NumPy geometry checks** automatically. No operator clicks or manual bag outlines are required. The no-login model sandbox above remains detection-only.

```bash
pip install -r requirements-sam2.txt
python -m app.prepare_sam2
python -m app.migrate
```

Set `AUTO_CHECK_ENABLED=true` and use `ROBOFLOW_MODEL_ID=krittin-wankundee/pallet-detection-zmufh-3-rfdetr-small-t1` (requires `paper-bag-stack` and `bag-layer` labels). Restart the backend. The preparation command downloads public official safetensors to ignored `.model-cache/` and records the model revision. Inspections use local weights without runtime downloads. See the [official SAM2 API](https://huggingface.co/docs/transformers/model_doc/sam2).

The supplied notebook's provisional defaults are 8 layers, ≤3° whole-stack tilt and ≤4% adjacent-layer center/edge offset. They are versioned with each report, **not UBE-approved production tolerances**. Configure `AUTO_CHECK_EXPECTED_LAYERS`, `AUTO_CHECK_MAX_TILT_DEG`, `AUTO_CHECK_MAX_OFFSET_PERCENT`, and `AUTO_CHECK_CAMERA_ROLL_DEG` for the agreed product/camera setup.

Reports persist in `ai_results.measurements.automatic_check` alongside original detector evidence. The UI displays layer boxes, the validated whole-stack outline, centerline, per-pair offsets and unavailable-check reasons. PASS requires every check; known measured failures produce FAIL even when another check is unavailable; otherwise insufficient evidence produces CANNOT_INSPECT/AI REVIEW. A human still confirms the final decision. Detection confidence is not calibrated quality confidence.

The browser submits `image?background=true` (202), then polls the inspection. Failed jobs retain their image and allow an explicit owner/admin retry via `POST /inspections/{id}/analyze`; completed/running jobs do not trigger duplicate inference. Background work runs in the API process; interrupted jobs can be reclaimed after 15 minutes. Use a durable queue for production workloads. Retries can consume additional Roboflow credits. The original blocking upload API remains compatible.

Upload claims and result writes use short worker-thread transactions. The API releases its database connection before inference in both upload modes; concurrent submissions claim an inspection only once. A failed blocking upload restores the original draft/no-image behavior. Background failures retain their image for retry.

History uses authenticated `GET /api/v1/inspections/page` with `offset`, `limit` (default 50, maximum 200), `search`, `decision` (`all`, `pass`, `fail`, `review`), `date_from`, `date_to`, `material`, and `q` (lot/pallet search). It returns `{items, total, offset, limit}`; the original list endpoint is unchanged. Dates are inclusive Asia/Bangkok calendar dates. `GET /api/v1/dashboard/analytics` accepts `date_from`, `date_to`, optional `material`/`q`, and returns database aggregates for the selected and equal-length previous periods, plus at most ten detailed recent records. Dashboard aggregation never downloads all inspection evidence into the API or browser. Five-topic check rates retain their existing unavailable semantics until the live API supplies those observations.

These image-space checks do not establish hidden bag interlocking, wrapping quality, physical 3D stability or perspective calibration. Previous manually annotated geometry remains available in history. Full Thai setup and limitations: [SUPABASE_SETUP.md](SUPABASE_SETUP.md).

A separate inference implementation can still be selected using `AI_PROVIDER=service` and `AI_SERVICE_URL`; explicit mock mode does not fabricate automatic checks.

## 8. What must be added before factory production use

- Additional versioned Alembic migrations for future schema changes (the initial migration now supports fresh databases and the original starter schema)
- Database uniqueness/concurrency design and transaction strategy for simultaneous inspectors
- Formal QA-approved tolerances and versioned rules; robust annotated-image storage
- Image-type hardening, virus scanning as appropriate, lifecycle management, object storage/backups
- HTTPS, secret manager, audit logs, access reviews, safe admin provisioning, rate limits
- Logging, monitoring, alerting, automated tests and backups
- Inference timeouts, retry policy, idempotency, offline/failure workflow
- Connection to real UBE lot/master-data sources and authorization requirements

## 9. Offline tests

Run from this repository's root. Tests explicitly isolate their configuration,
database and uploads; they do not use your development database or call Roboflow:

```bash
python -m tests.smoke_flow
python -m unittest tests.test_roboflow -v
python -m unittest tests.test_inspection_async -v
python -m unittest tests.test_inspection_queries -v
```

Run test modules in separate processes as shown: each module initializes its own temporary database/configuration before importing the application.

This tests login, product creation, lot creation, image upload, mock AI, human confirmation, and the dashboard.

The Roboflow suite uses HTTPX `MockTransport` to verify request encoding,
credential handling, detection persistence, target-label filtering, errors,
authentication, protected-image access and phone orientation. These are software
tests, not a live inference or accuracy test. Live verification needs your own
key, accessible model and an intentionally selected test photo.

### Multiple inspection photos

The upload endpoint accepts required multipart `file` plus up to three optional `files` parts (1–4 photos total). Every submitted photo is normalized, analyzed sequentially, and retained. Any FAIL makes the overall decision FAIL; otherwise any REVIEW makes it REVIEW; PASS requires all submitted photos to pass. Per-photo reports are stored in `measurements.image_results`; a single photo keeps the existing result format. Photo retrieval accepts `?sequence=1` through `4` and the inspection response lists its images. Additional normalized files use the primary stored path plus `.2`, `.3`, or `.4` and their format extension; keep these files together when backing up uploads. Manual geometry still applies to the first photo.
