"""Offline regressions for upload concurrency and event-loop/database isolation."""
import asyncio
import os
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

import httpx
from PIL import Image

TEMP = tempfile.TemporaryDirectory()
ENV = patch.dict(os.environ, {
    "DATABASE_URL": f"sqlite:///{TEMP.name}/async.db",
    "UPLOAD_DIR": f"{TEMP.name}/uploads",
    "JWT_SECRET_KEY": "offline-async-test-secret",
    "AI_PROVIDER": "mock",
})
ENV.start()

from fastapi.testclient import TestClient
from sqlalchemy import event
from app.database import SessionLocal, engine
from app.main import app
from app.models import User
from app.security import make_token


def tearDownModule():
    engine.dispose()
    ENV.stop()
    TEMP.cleanup()


RESULT = {"decision": "REVIEW", "confidence": 0, "measurements": {}, "model_version": "offline", "defect_type": None}
IMAGE = BytesIO()
Image.new("RGB", (100, 100), "white").save(IMAGE, "PNG")
BLOB = IMAGE.getvalue()


class AsyncUploadTests(unittest.TestCase):
    def setUp(self):
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        with SessionLocal() as db:
            user = User(username=uuid4().hex, role="inspector", password_hash="unused")
            db.add(user)
            db.commit()
            self.headers = {"Authorization": "Bearer " + make_token(user)}

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def create_path(self):
        identifier = self.client.post("/api/v1/inspections", headers=self.headers, json={}).json()["inspection_id"]
        return f"/api/v1/inspections/{identifier}"

    def test_database_work_is_off_loop_and_inference_holds_no_connection(self):
        def assert_off_loop(*args):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return
            self.fail("Synchronous SQL ran on the ASGI event loop")

        for background in (False, True):
            with self.subTest(background=background):
                path = self.create_path()

                async def analyze(*args):
                    self.assertEqual(engine.pool.checkedout(), 0, "Inference retained a database connection")
                    # Another request must remain usable while inference is awaiting.
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                        record = await client.get(path, headers=self.headers)
                        self.assertEqual(record.json()["status"], "PROCESSING")
                        duplicate = await client.post(path + "/image", headers=self.headers,
                                                      files={"file": ("photo.png", BLOB, "image/png")})
                        self.assertEqual(duplicate.status_code, 409)
                    return RESULT

                event.listen(engine, "before_cursor_execute", assert_off_loop)
                try:
                    with patch("app.routers.inspections.analyze_image", new=analyze):
                        response = self.client.post(path + ("/image?background=true" if background else "/image"),
                                                    headers=self.headers, files={"file": ("photo.png", BLOB, "image/png")})
                    self.assertEqual(response.status_code, 202 if background else 200, response.text)
                finally:
                    event.remove(engine, "before_cursor_execute", assert_off_loop)
                self.assertEqual(self.client.get(path, headers=self.headers).json()["status"], "AI_PROCESSED")

    def test_racing_uploads_start_only_one_inference(self):
        path = self.create_path()

        async def race():
            release = asyncio.Event()
            calls = 0

            async def analyze(*args):
                nonlocal calls
                calls += 1
                await release.wait()
                return RESULT

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                with patch("app.routers.inspections.analyze_image", new=analyze):
                    requests = [asyncio.create_task(client.post(path + "/image", headers=self.headers,
                                files={"file": ("photo.png", BLOB, "image/png")})) for _ in range(2)]
                    try:
                        done, _ = await asyncio.wait(requests, timeout=5, return_when=asyncio.FIRST_COMPLETED)
                        self.assertTrue(done, "A duplicate upload blocked the event loop")
                        self.assertEqual(next(iter(done)).result().status_code, 409)
                    finally:
                        release.set()
                    responses = await asyncio.gather(*requests)
            self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
            self.assertEqual(calls, 1)

        asyncio.run(race())

    def test_one_to_four_images_are_analyzed_stored_and_retrievable(self):
        from unittest.mock import AsyncMock
        for count in range(1, 5):
            with self.subTest(count=count):
                path = self.create_path()
                results = [{**RESULT, "decision": "FAIL" if i == count - 1 else "PASS"} for i in range(count)]
                files = [("file" if i == 0 else "files", (f"photo-{i}.png", BLOB, "image/png")) for i in range(count)]
                with patch("app.routers.inspections.analyze_image", new=AsyncMock(side_effect=results)) as analyze:
                    response = self.client.post(path + "/image?background=true", headers=self.headers, files=files)
                self.assertEqual(response.status_code, 202, response.text)
                self.assertEqual(analyze.await_count, count)
                record = self.client.get(path, headers=self.headers).json()
                self.assertEqual(len(record["images"]), count)
                self.assertEqual(record["ai_result"]["decision"], "FAIL")
                for sequence in range(1, count + 1):
                    image = self.client.get(path + f"/image?sequence={sequence}", headers=self.headers)
                    self.assertEqual(image.status_code, 200)
                    self.assertEqual(image.headers["content-type"], "image/png")
                self.assertEqual(self.client.get(path + "/image?sequence=5", headers=self.headers).status_code, 422)
        path = self.create_path()
        files = [("file" if i == 0 else "files", ("photo.png", BLOB, "image/png")) for i in range(5)]
        with patch("app.routers.inspections.analyze_image", new=AsyncMock()) as analyze:
            self.assertEqual(self.client.post(path + "/image", headers=self.headers, files=files).status_code, 422)
            analyze.assert_not_awaited()

    def test_multiple_image_failure_restores_draft_and_removes_all_uploaded_files(self):
        from pathlib import Path
        from unittest.mock import AsyncMock
        from app.services.roboflow import AIServiceError
        path = self.create_path()
        before = set(Path(TEMP.name).rglob("*.png"))
        files = [("file", ("one.png", BLOB, "image/png")), ("files", ("two.png", BLOB, "image/png"))]
        with patch("app.routers.inspections.analyze_image", new=AsyncMock(side_effect=[RESULT, AIServiceError("unavailable")])):
            response = self.client.post(path + "/image", headers=self.headers, files=files)
        self.assertEqual(response.status_code, 503)
        record = self.client.get(path, headers=self.headers).json()
        self.assertEqual(record["status"], "DRAFT")
        self.assertEqual(record["images"], [])
        self.assertEqual(set(Path(TEMP.name).rglob("*.png")), before)

    def test_geometry_releases_connection_and_rechecks_state_after_cpu_work(self):
        path = self.create_path()

        async def analyze(*args):
            return RESULT

        with patch("app.routers.inspections.analyze_image", new=analyze):
            self.assertEqual(self.client.post(path + "/image", headers=self.headers,
                             files={"file": ("photo.png", BLOB, "image/png")}).status_code, 200)

        def measure(*args):
            self.assertEqual(engine.pool.checkedout(), 0, "Geometry CPU work retained a database connection")
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            response = self.client.post(path + "/confirm", headers=self.headers, json={"decision": "PASS"})
            self.assertEqual(response.status_code, 200, response.text)
            return {}

        annotation = {"image_width": 100, "image_height": 100, "baseline": [[0, 90], [99, 90]],
                      "bags": [{"layer": 1, "points": [[10, 10], [80, 10], [80, 80], [10, 80]]}], "verified": True}
        with patch("app.routers.inspections.measure", new=measure):
            response = self.client.post(path + "/geometry", headers=self.headers, json=annotation)
        self.assertEqual(response.status_code, 409, response.text)
        record = self.client.get(path, headers=self.headers).json()
        self.assertEqual(record["status"], "CONFIRMED")
        self.assertIsNone(record["geometry_result"])


if __name__ == "__main__":
    unittest.main()
