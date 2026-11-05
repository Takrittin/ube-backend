"""Offline contract and API tests; no cloud requests, real keys, or real data."""
import base64
import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from PIL import Image

TEMP = tempfile.TemporaryDirectory()
ENV = patch.dict(os.environ, {
    "DATABASE_URL": "sqlite:///" + TEMP.name + "/tests.db",
    "UPLOAD_DIR": TEMP.name + "/uploads",
    "JWT_SECRET_KEY": "offline-test-secret-never-used-for-production",
    "AI_PROVIDER": "roboflow",
    "AUTO_CHECK_ENABLED": "false",
    "ROBOFLOW_API_KEY": "offline-test-key",
    "ROBOFLOW_MODEL_ID": "krittin-wankundee/pallet-detection-zmufh-4-rfdetr-small-t1",
    "ROBOFLOW_CONFIDENCE": "0.25",
    "ROBOFLOW_TARGET_CLASSES": "pallet,pallet-detection,wooden-pallet",
    "AI_SERVICE_URL": "",
})
ENV.start()

from fastapi.testclient import TestClient
from sqlalchemy import select
from app import config
from app.database import SessionLocal, engine
from app.main import app
from app.models import AIResult, Inspection, InspectionImage, User
from app.routers.inspections import prepare_image
from app.security import hash_password, make_token
from app.services.ai import analyze_image
from app.services.roboflow import AIServiceError, detect_pallet

REAL_ASYNC_CLIENT = httpx.AsyncClient


def tearDownModule():
    engine.dispose()
    ENV.stop()
    TEMP.cleanup()


def photo(fmt="JPEG", size=(160, 100), exif=None):
    output = BytesIO()
    kwargs = {"exif": exif} if exif else {}
    Image.new("RGB", size, "#b29d72").save(output, format=fmt, **kwargs)
    return output.getvalue()


def result(predictions=None):
    return {"image": {"width": 160, "height": 100}, "predictions": predictions if predictions is not None else [
        {"class": "pallet", "confidence": 0.93, "x": 80, "y": 75, "width": 120, "height": 30},
        {"class": "paper-bag-stack", "confidence": 0.98, "x": 80, "y": 40, "width": 100, "height": 55},
    ]}


def cloud(handler):
    return patch("app.services.roboflow.httpx.AsyncClient", side_effect=lambda *a, **kw: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler)))


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_contract_and_real_detection_evidence(self):
        blob = photo()
        def handler(request):
            self.assertEqual(str(request.url).split("?")[0], "https://serverless.roboflow.com/" + config.ROBOFLOW_MODEL_ID)
            self.assertEqual(request.url.params["confidence"], "25.0")
            self.assertEqual(request.headers["authorization"], "Bearer offline-test-key")
            self.assertNotIn(config.ROBOFLOW_API_KEY, str(request.url))
            self.assertEqual(base64.b64decode(request.content), blob)
            return httpx.Response(200, json=result())
        with cloud(handler):
            output = await analyze_image(blob, "image/jpeg")
        self.assertEqual(output["decision"], "REVIEW")
        self.assertEqual(output["confidence"], 0.93)
        self.assertTrue(output["measurements"]["pallet_detected"])
        self.assertEqual(output["measurements"]["pallet_count"], 1)
        self.assertEqual(len(output["measurements"]["predictions"]), 2)
        self.assertNotIn(config.ROBOFLOW_API_KEY, json.dumps(output))

    async def test_non_pallet_and_low_confidence_are_not_pallet_identification(self):
        predictions = result()["predictions"]
        predictions[0]["confidence"] = 0.1
        with cloud(lambda request: httpx.Response(200, json=result(predictions))):
            output = await detect_pallet(photo())
        self.assertFalse(output["measurements"]["pallet_detected"])
        self.assertEqual(output["confidence"], 0)
        self.assertEqual(len(output["measurements"]["predictions"]), 1)

    async def test_empty_detections_are_real_review_not_a_fake_pass(self):
        with cloud(lambda request: httpx.Response(200, json=[result([])])):
            output = await detect_pallet(photo())
        self.assertEqual(output["decision"], "REVIEW")
        self.assertFalse(output["measurements"]["pallet_detected"])
        self.assertEqual(output["measurements"]["provider"], "roboflow")

    async def test_missing_key_does_not_call_cloud_or_fall_back_to_mock(self):
        with patch.object(config, "ROBOFLOW_API_KEY", ""), cloud(lambda request: self.fail("Unexpected network request")):
            with self.assertRaisesRegex(AIServiceError, "ROBOFLOW_API_KEY"):
                await detect_pallet(photo())

    async def test_cloud_errors_are_actionable_and_do_not_echo_provider_secrets(self):
        for status in (401, 403, 404, 402, 429, 500):
            with self.subTest(status=status), cloud(lambda request: httpx.Response(status, text=config.ROBOFLOW_API_KEY)):
                with self.assertRaises(AIServiceError) as caught:
                    await detect_pallet(photo())
                self.assertNotIn(config.ROBOFLOW_API_KEY, str(caught.exception))

    async def test_timeout(self):
        def handler(request):
            raise httpx.ReadTimeout("provider-secret-must-not-escape", request=request)
        with cloud(handler), self.assertRaisesRegex(AIServiceError, "timed out"):
            await detect_pallet(photo())

    async def test_malformed_output_is_not_saved_as_success(self):
        invalid = [{}, {"predictions": [], "image": {"width": 0, "height": 100}}, result()]
        invalid[-1]["predictions"][0]["confidence"] = 2
        for payload in invalid:
            with self.subTest(payload=payload), cloud(lambda request: httpx.Response(200, json=payload)):
                with self.assertRaisesRegex(AIServiceError, "invalid detection"):
                    await detect_pallet(photo())

    async def test_non_finite_coordinates_are_rejected(self):
        payload = result()
        payload["predictions"][0]["x"] = float("nan")
        with cloud(lambda request: httpx.Response(200, content=json.dumps(payload), headers={"Content-Type": "application/json"})):
            with self.assertRaises(AIServiceError):
                await detect_pallet(photo())

    async def test_custom_target_label(self):
        with patch.object(config, "ROBOFLOW_TARGET_CLASSES", {"paper-bag-stack"}), cloud(lambda request: httpx.Response(200, json=result())):
            output = await detect_pallet(photo())
        self.assertEqual(output["confidence"], 0.98)


class APITests(unittest.TestCase):
    def setUp(self):
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        suffix = uuid4().hex
        with SessionLocal() as db:
            user = User(username="admin-" + suffix, role="admin", password_hash=hash_password("offline-password-123"))
            other = User(username="other-" + suffix, role="inspector", password_hash="not-used")
            db.add_all([user, other]); db.commit()
            self.headers = {"Authorization": "Bearer " + make_token(user)}
            self.other_headers = {"Authorization": "Bearer " + make_token(other)}
        product = self.client.post("/api/v1/products", headers=self.headers, json={"sku": suffix, "name": "Test pallet"}).json()
        self.lot = "LOT-" + suffix
        self.client.post("/api/v1/lots", headers=self.headers, json={"lot_code": self.lot, "product_id": product["id"]})
        self.id = self.client.post("/api/v1/inspections", headers=self.headers, json={"lot_code": self.lot}).json()["inspection_id"]

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def upload(self, content=None, headers=None):
        return self.client.post(f"/api/v1/inspections/{self.id}/image", headers=headers or self.headers,
                                files={"file": ("pallet.jpg", photo() if content is None else content, "image/jpeg")})

    def test_ui_and_authenticated_configuration_status(self):
        self.assertEqual(self.client.get("/test-ui/").status_code, 200)
        self.assertEqual(self.client.get("/test-ui/app.js").status_code, 200)
        self.assertEqual(self.client.get("/test-ui/styles.css").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/ai/status").status_code, 401)
        response = self.client.get("/api/v1/ai/status", headers=self.headers)
        self.assertTrue(response.json()["configured"])
        self.assertNotIn(config.ROBOFLOW_API_KEY, response.text)

    def test_detection_persists_boxes_and_human_decision_separately(self):
        with cloud(lambda request: httpx.Response(200, json=result())):
            response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.client.get(f"/api/v1/inspections/{self.id}", headers=self.headers).json()
        self.assertEqual(saved["ai_result"]["measurements"]["pallet_count"], 1)
        self.assertEqual(saved["status"], "AI_PROCESSED")
        self.assertEqual(self.client.get(f"/api/v1/inspections/{self.id}/image").status_code, 401)
        image = self.client.get(f"/api/v1/inspections/{self.id}/image", headers=self.headers)
        self.assertEqual(image.status_code, 200)
        confirmed = self.client.post(f"/api/v1/inspections/{self.id}/confirm", headers=self.headers, json={"decision": "PASS", "notes": "Manually checked"})
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["ai_decision"], "REVIEW")
        self.assertEqual(confirmed.json()["final_decision"], "PASS")
        with cloud(lambda request: self.fail("Repeated upload must not call cloud")):
            self.assertEqual(self.upload().status_code, 409)

    def test_failure_keeps_draft_and_leaves_no_image_or_ai_record(self):
        with cloud(lambda request: httpx.Response(403, text="private provider error")):
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        saved = self.client.get(f"/api/v1/inspections/{self.id}", headers=self.headers).json()
        self.assertEqual(saved["status"], "DRAFT")
        self.assertFalse(saved["has_image"])
        self.assertIsNone(saved["ai_result"])
        with SessionLocal() as db:
            self.assertIsNone(db.scalar(select(AIResult).where(AIResult.inspection_id == self.id)))
            self.assertIsNone(db.scalar(select(InspectionImage).where(InspectionImage.inspection_id == self.id)))

    def test_missing_configuration_and_invalid_inputs_do_not_call_cloud(self):
        with cloud(lambda request: self.fail("Unexpected network request")):
            self.assertEqual(self.upload(headers=self.other_headers).status_code, 403)
            self.assertEqual(self.upload(content=b"not an image").status_code, 415)
            self.assertEqual(self.upload(content=b"x" * (10 * 1024 * 1024 + 1)).status_code, 413)
            with patch.object(config, "ROBOFLOW_API_KEY", ""):
                self.assertFalse(self.client.get("/api/v1/ai/status", headers=self.headers).json()["configured"])
                self.assertEqual(self.upload().status_code, 503)

    def test_phone_orientation_is_normalized_before_storage_and_inference(self):
        exif = Image.Exif(); exif[274] = 6
        normalized, extension, media_type = prepare_image(photo(size=(160, 100), exif=exif))
        with Image.open(BytesIO(normalized)) as image:
            self.assertEqual(image.size, (100, 160))
            self.assertNotIn(274, image.getexif())
        self.assertEqual((extension, media_type), (".jpg", "image/jpeg"))


class ModelSandboxTests(unittest.TestCase):
    def setUp(self):
        self.flag = patch.object(config, "ENABLE_MODEL_TEST_UI", True)
        self.flag.start()
        self.context = TestClient(app)
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.flag.stop()

    def test_anonymous_detection_has_no_inspection_or_file_side_effects(self):
        with SessionLocal() as db:
            before = list(db.scalars(select(Inspection.id)))
        files = set(Path(config.UPLOAD_DIR).glob("*"))
        # The sandbox must run real Roboflow regardless of the inspection provider.
        with patch.object(config, "AI_PROVIDER", "mock"), cloud(lambda request: httpx.Response(200, json=result())):
            response = self.client.post("/api/v1/model-test/detect", headers={"Origin": "http://testserver"}, files={"file": ("test.jpg", photo(), "image/jpeg")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(response.json()["ai_result"]["measurements"]["pallet_detected"])
        encoded = response.json()["image_data_url"].split(",", 1)[1]
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            self.assertEqual(image.size, (160, 100))
        with SessionLocal() as db:
            self.assertEqual(list(db.scalars(select(Inspection.id))), before)
        self.assertEqual(set(Path(config.UPLOAD_DIR).glob("*")), files)
        self.assertEqual(self.client.get("/api/v1/users/me").status_code, 401)

    def test_status_has_no_key_and_does_not_require_login(self):
        response = self.client.get("/api/v1/model-test/status")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(config.ROBOFLOW_API_KEY, response.text)

    def test_disabled_or_foreign_origin_cannot_trigger_cloud_calls(self):
        with cloud(lambda request: self.fail("Unexpected cloud call")):
            with patch.object(config, "ENABLE_MODEL_TEST_UI", False):
                self.assertEqual(self.client.get("/api/v1/model-test/status").status_code, 404)
                self.assertEqual(self.client.post("/api/v1/model-test/detect", files={"file": ("test.jpg", photo())}).status_code, 404)
            response = self.client.post("/api/v1/model-test/detect", headers={"Origin": "https://unrelated.example"}, files={"file": ("test.jpg", photo())})
            self.assertEqual(response.status_code, 403)

    def test_bad_upload_and_missing_key_have_clear_errors(self):
        response = self.client.post("/api/v1/model-test/detect", files={"file": ("test.jpg", b"not an image")})
        self.assertEqual(response.status_code, 415)
        with patch.object(config, "ROBOFLOW_API_KEY", ""):
            response = self.client.post("/api/v1/model-test/detect", files={"file": ("test.jpg", photo())})
            self.assertEqual(response.status_code, 503)
            self.assertIn("ROBOFLOW_API_KEY", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
