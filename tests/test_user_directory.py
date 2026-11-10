"""User directory contracts on isolated SQLite; no external accounts."""
import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import timedelta

TEMP = tempfile.TemporaryDirectory()
ENV = patch.dict(os.environ, {"DATABASE_URL": f"sqlite:///{TEMP.name}/users.db", "JWT_SECRET_KEY": "directory-tests-only-secret", "AI_PROVIDER": "mock"})
ENV.start()
from fastapi.testclient import TestClient
from app.main import app
from app.database import Base, SessionLocal, engine
from app.models import User, now_utc
from app.security import make_token


def tearDownModule():
    engine.dispose()
    ENV.stop()
    TEMP.cleanup()


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        with SessionLocal() as db:
            self.admin = User(username="admin", full_name="Admin", password_hash="unused", role="admin")
            self.inspector = User(username="inspector", full_name="Inspector", password_hash="unused", role="inspector")
            db.add_all([self.admin, self.inspector]); db.commit()
            self.headers = {"Authorization": "Bearer " + make_token(self.admin)}
            self.inspector_headers = {"Authorization": "Bearer " + make_token(self.inspector)}

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def test_pagination_search_filters_sort_and_authorization(self):
        with SessionLocal() as db:
            for i in range(25):
                db.add(User(username=f"member-{i:02}", full_name=f"Member {i:02}", password_hash="unused", role="qa", is_active=i % 2 == 0, email=f"member{i}@example.com"))
            db.commit()
        url = "/api/v1/users/page"
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.inspector_headers).status_code, 403)
        page = self.client.get(url, headers=self.headers, params={"search": "member", "role": "qa", "sort": "name", "direction": "asc", "offset": 20, "limit": 20}).json()
        self.assertEqual(page["total"], 25)
        self.assertEqual(len(page["items"]), 5)
        self.assertEqual(page["items"][0]["full_name"], "Member 20")
        active = self.client.get(url, headers=self.headers, params={"role": "qa", "is_active": "true"}).json()
        self.assertEqual(active["total"], 13)
        self.assertEqual(self.client.get(url, headers=self.headers, params={"search": "member24@example.com"}).json()["total"], 1)
        self.assertEqual(self.client.get(url, headers=self.headers, params={"search": "%"}).json()["total"], 0)
        self.assertEqual(self.client.get(url, headers=self.headers, params={"sort": "password_hash"}).status_code, 422)
        self.assertEqual(self.client.get(url, headers=self.headers, params={"limit": 101}).status_code, 422)
        self.assertNotIn("password_hash", str(page))

    def test_create_edit_email_uniqueness_activation_and_self_protection(self):
        payload = {"username": "new-member", "password": "test-password-123", "full_name": "New Member", "email": " Person@Example.COM ", "role": "qa"}
        created = self.client.post("/api/v1/users", headers=self.headers, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        user = created.json()
        self.assertEqual(user["email"], "person@example.com")
        self.assertIsNotNone(user["created_at"])
        self.assertIsNone(user["last_active_at"])
        duplicate = self.client.post("/api/v1/users", headers=self.headers, json={**payload, "username": "another"})
        self.assertEqual(duplicate.status_code, 409)
        path = f"/api/v1/users/{user['id']}"
        self.assertEqual(self.client.patch(path, headers=self.headers, json={"email": "bad-email"}).status_code, 422)
        edited = self.client.patch(path, headers=self.headers, json={"full_name": "Updated", "email": None, "role": "inspector"})
        self.assertEqual(edited.status_code, 200)
        self.assertIsNone(edited.json()["email"])
        for active in [False, True]:
            self.assertEqual(self.client.patch(path, headers=self.headers, json={"is_active": active}).json()["is_active"], active)
        for payload in [{"is_active": False}, {"role": "qa"}]:
            self.assertEqual(self.client.patch(f"/api/v1/users/{self.admin.id}", headers=self.headers, json=payload).status_code, 409)

    def test_activity_is_recorded_and_throttled(self):
        first = self.client.get("/api/v1/users/me", headers=self.headers).json()["last_active_at"]
        self.assertIsNotNone(first)
        second = self.client.get("/api/v1/users/me", headers=self.headers).json()["last_active_at"]
        self.assertEqual(first, second)
        with SessionLocal() as db:
            user = db.get(User, self.admin.id)
            user.last_active_at = now_utc() - timedelta(minutes=6)
            db.commit()
        third = self.client.get("/api/v1/users/me", headers=self.headers).json()["last_active_at"]
        self.assertNotEqual(third, first)


if __name__ == "__main__": unittest.main()
