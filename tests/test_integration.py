"""API contract, authorization and additive migration checks. No cloud calls."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

TEMP = tempfile.TemporaryDirectory()
ENV = patch.dict(os.environ, {
    "DATABASE_URL": f"sqlite:///{TEMP.name}/integration.db",
    "UPLOAD_DIR": f"{TEMP.name}/uploads",
    "JWT_SECRET_KEY": "integration-test-secret-not-for-production",
    "AI_PROVIDER": "mock", "ROBOFLOW_API_KEY": "", "AI_SERVICE_URL": "",
})
ENV.start()

from fastapi.testclient import TestClient
from app.database import SessionLocal, engine
from app.main import app
from app.models import User
from app.security import hash_password


def tearDownModule():
    engine.dispose()
    ENV.stop()
    TEMP.cleanup()


class ConnectedWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = TestClient(app)
        cls.client = cls.context.__enter__()
        with SessionLocal() as db:
            db.add(User(username="integration-admin", full_name="Admin", role="admin",
                        password_hash=hash_password("integration-password")))
            db.commit()
        cls.headers = cls.sign_in("integration-admin")

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    @classmethod
    def sign_in(cls, username):
        response = cls.client.post("/api/v1/auth/token", data={"username": username, "password": "integration-password"})
        assert response.status_code == 200, response.text
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    def test_full_contract_and_persistent_history(self):
        c, h = self.client, self.headers
        product = c.post("/api/v1/products", headers=h, json={"sku": "INT-01", "name": "Paper Bag", "bag_type": "paper"})
        self.assertEqual(product.status_code, 201, product.text)
        lot = c.post("/api/v1/lots", headers=h, json={"lot_code": "INT-LOT", "product_id": product.json()["id"],
                    "production_date": "2026-11-03", "quantity": 50})
        self.assertEqual(lot.status_code, 201, lot.text)
        self.assertEqual(c.get("/api/v1/lots", headers=h).json()[0]["production_date"], "2026-11-03")
        self.assertEqual(c.get("/api/v1/products", headers=h).json()[0]["bag_type"], "paper")
        created = c.post("/api/v1/inspections", headers=h, json={"lot_code": "INT-LOT", "pallet_code": "P001",
                         "material_code": "M001", "qr_raw": "INT-LOT001|M001"})
        self.assertEqual(created.status_code, 201, created.text)
        identifier = created.json()["inspection_id"]
        image = BytesIO()
        Image.new("RGB", (30, 30), "white").save(image, "JPEG")
        upload = c.post(f"/api/v1/inspections/{identifier}/image", headers=h,
                        files={"file": ("test.jpg", image.getvalue(), "image/jpeg")})
        self.assertEqual(upload.status_code, 200, upload.text)
        self.assertEqual(upload.json()["ai_result"]["decision"], "REVIEW")
        confirmed = c.post(f"/api/v1/inspections/{identifier}/confirm", headers=h,
                           json={"decision": "PASS", "notes": "Reviewed by inspector"})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        record = c.get("/api/v1/inspections?offset=0&limit=1", headers=h).json()[0]
        saved = confirmed.json()["inspection"]
        # SQLite reloads timestamps without offsets; both represent the same UTC instant.
        saved["final_result"]["confirmed_at"] = saved["final_result"]["confirmed_at"].removesuffix("+00:00")
        self.assertEqual(saved, record)
        self.assertEqual(record["pallet_code"], "P001")
        self.assertEqual(record["material_code"], "M001")
        self.assertEqual(record["qr_raw"], "INT-LOT001|M001")
        self.assertEqual(record["lot_id"], lot.json()["id"])
        self.assertEqual(record["inspector_name"], "Admin")
        self.assertEqual(record["final_result"]["decision"], "PASS")
        self.assertIsNotNone(record["image_id"])
        self.assertEqual(c.get(f"/api/v1/inspections/{identifier}/image", headers=h).status_code, 200)
        self.assertEqual(c.get(f"/api/v1/inspections/{identifier}/image").status_code, 401)
        self.assertEqual(c.get("/api/v1/inspections?offset=100&limit=1", headers=h).json(), [])

    def test_admin_user_management_and_role_enforcement(self):
        c, h = self.client, self.headers
        account = c.post("/api/v1/users", headers=h, json={"username": "integration-inspector", "password": "integration-password",
                         "full_name": "Inspector Name", "role": "inspector"})
        self.assertEqual(account.status_code, 201, account.text)
        identifier = account.json()["id"]
        inspector = self.sign_in("integration-inspector")
        self.assertEqual(c.get("/api/v1/users/me", headers=inspector).json()["full_name"], "Inspector Name")
        for path in ("/api/v1/users",):
            self.assertEqual(c.get(path, headers=inspector).status_code, 403)
        self.assertEqual(c.post("/api/v1/products", headers=inspector, json={"sku": "NO", "name": "No"}).status_code, 403)
        self.assertEqual(c.patch(f"/api/v1/users/{identifier}", headers=h, json={"role": "qa"}).status_code, 200)
        # Existing tokens must immediately use the new role from the database.
        self.assertEqual(c.post("/api/v1/inspections", headers=inspector, json={"lot_code": "anything"}).status_code, 403)
        self.assertEqual(c.delete(f"/api/v1/users/{identifier}", headers=h).status_code, 204)
        self.assertEqual(c.get("/api/v1/users/me", headers=inspector).status_code, 401)
        users = c.get("/api/v1/users", headers=h).json()
        self.assertNotIn(identifier, [u["id"] for u in users])
        self.assertNotIn("password_hash", users[0])
        own_id = c.get("/api/v1/users/me", headers=h).json()["id"]
        self.assertEqual(c.delete(f"/api/v1/users/{own_id}", headers=h).status_code, 409)
        self.assertEqual(c.patch(f"/api/v1/users/{own_id}", headers=h, json={"role": "inspector"}).status_code, 409)

    def test_lists_require_authentication(self):
        for path in ("products", "lots", "inspections", "users"):
            self.assertEqual(self.client.get(f"/api/v1/{path}").status_code, 401)

    def test_inspection_without_lot_can_be_analyzed_and_confirmed(self):
        c, h = self.client, self.headers
        created = c.post("/api/v1/inspections", headers=h, json={})
        self.assertEqual(created.status_code, 201, created.text)
        identifier = created.json()["inspection_id"]
        image = BytesIO()
        Image.new("RGB", (30, 30), "white").save(image, "JPEG")
        self.assertEqual(c.post(f"/api/v1/inspections/{identifier}/image", headers=h,
            files={"file": ("test.jpg", image.getvalue(), "image/jpeg")}).status_code, 200)
        self.assertEqual(c.post(f"/api/v1/inspections/{identifier}/confirm", headers=h,
            json={"decision": "PASS", "notes": "Checked without lot", "defects": [" ถุงสินค้าเสียหาย ", "ถุงสินค้าเสียหาย"]}).status_code, 200)
        record = c.get(f"/api/v1/inspections/{identifier}", headers=h).json()
        self.assertEqual(record["final_result"]["defects"], ["ถุงสินค้าเสียหาย"])
        self.assertIsNone(record["ai_result"]["defect_type"])
        self.assertIsNone(record["lot_id"])
        self.assertIsNone(record["lot_code"])
        self.assertEqual(record["final_result"]["decision"], "PASS")

    def test_invalid_defects_do_not_confirm_inspection(self):
        c, h = self.client, self.headers
        identifier = c.post("/api/v1/inspections", headers=h, json={}).json()["inspection_id"]
        for defects in ([" "], ["x" * 101], ["x"] * 11):
            response = c.post(f"/api/v1/inspections/{identifier}/confirm", headers=h,
                              json={"decision": "FAIL", "defects": defects})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertIsNone(c.get(f"/api/v1/inspections/{identifier}", headers=h).json()["final_result"])

    def test_geometry_is_saved_separately_and_locked_after_confirmation(self):
        c, h = self.client, self.headers
        identifier = c.post("/api/v1/inspections", headers=h, json={}).json()["inspection_id"]
        image = BytesIO()
        Image.new("RGB", (800, 600), "white").save(image, "PNG")
        self.assertEqual(c.post(f"/api/v1/inspections/{identifier}/image", headers=h,
            files={"file": ("test.png", image.getvalue(), "image/png")}).status_code, 200)
        path = f"/api/v1/inspections/{identifier}/geometry"
        payload = {"image_width": 800, "image_height": 600, "baseline": [[20, 500], [780, 500]],
                   "bags": [{"points": [[100, 100], [300, 100], [300, 200], [100, 200]], "layer": 1}], "verified": True}
        payload["bags"].extend([
            {"points": [[350, 100], [550, 100], [550, 200], [350, 200]], "layer": 1},
            {"points": [[200, 20], [400, 20], [400, 80], [200, 80]], "layer": 2},
            {"points": [[450, 20], [650, 20], [650, 80], [450, 80]], "layer": 2},
        ])
        self.assertEqual(c.post(path, json=payload).status_code, 401)
        self.assertEqual(c.post(path, headers=h, json={**payload, "verified": False}).status_code, 422)
        self.assertEqual(c.post(path, headers=h, json={**payload, "image_width": 200}).status_code, 422)
        c.post("/api/v1/users", headers=h, json={"username": "geometry-other", "password": "integration-password", "role": "inspector"})
        other = self.sign_in("geometry-other")
        self.assertEqual(c.post(path, headers=other, json=payload).status_code, 403)
        self.assertEqual(c.post(path + "/suggest", headers=other).status_code, 403)
        result = c.post(path, headers=h, json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["report"]["bags"][0]["tilt_deg"], 0)
        self.assertEqual(len(result.json()["report"]["layer_offsets"]), 1)
        self.assertEqual(c.post(path, headers=h, json=payload).status_code, 200)
        record = c.get(f"/api/v1/inspections/{identifier}", headers=h).json()
        self.assertEqual(record["geometry_result"]["annotation"], payload)
        self.assertEqual(record["ai_result"]["decision"], "REVIEW")
        self.assertNotIn("geometry", record["ai_result"]["measurements"])
        self.assertEqual(c.post(f"/api/v1/inspections/{identifier}/confirm", headers=h, json={"decision": "PASS"}).status_code, 200)
        self.assertEqual(c.post(path, headers=h, json=payload).status_code, 409)
        self.assertEqual(c.get(f"/api/v1/inspections/{identifier}", headers=h).json()["geometry_result"]["report"]["status"], "REVIEW")

    def test_unknown_lot_is_not_invented(self):
        response = self.client.post("/api/v1/inspections", headers=self.headers, json={"lot_code": "MISSING"})
        self.assertEqual(response.status_code, 404)


class MigrationTests(unittest.TestCase):
    def test_fresh_and_legacy_database_upgrade_preserves_existing_user(self):
        with tempfile.TemporaryDirectory() as folder:
            for legacy in (False, True):
                target = Path(folder) / ("legacy.db" if legacy else "fresh.db")
                if legacy:
                    with sqlite3.connect(target) as db:
                        db.execute("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, username VARCHAR(80) NOT NULL UNIQUE, "
                                   "password_hash VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL, is_active BOOLEAN NOT NULL)")
                        db.execute("INSERT INTO users VALUES ('legacy', 'old-user', 'hash', 'admin', 1)")
                env = {**os.environ, "DATABASE_URL": f"sqlite:///{target}"}
                for _ in range(2):
                    result = subprocess.run([sys.executable, "-m", "app.migrate"], env=env, capture_output=True, text=True,
                                            cwd=Path(__file__).resolve().parent.parent)
                    self.assertEqual(result.returncode, 0, result.stderr)
                with sqlite3.connect(target) as db:
                    self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone()[0], "0005_automatic_processing")
                    self.assertIn("qr_raw", [row[1] for row in db.execute("PRAGMA table_info(inspections)")])
                    if legacy:
                        self.assertEqual(db.execute("SELECT username, full_name FROM users").fetchone(), ("old-user", ""))


if __name__ == "__main__":
    unittest.main()
