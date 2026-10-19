"""Small end-to-end test using an isolated temporary SQLite database."""
import os
import tempfile
from io import BytesIO
from PIL import Image

with tempfile.TemporaryDirectory() as folder:
    os.environ["DATABASE_URL"] = "sqlite:///" + folder + "/test.db"
    os.environ["JWT_SECRET_KEY"] = "test-only-very-long-secret-not-for-production"
    os.environ["UPLOAD_DIR"] = folder + "/uploads"
    os.environ.pop("AI_SERVICE_URL", None)

    from fastapi.testclient import TestClient
    from app.database import SessionLocal
    from app.main import app
    from app.models import User
    from app.security import hash_password

    with TestClient(app) as client:
        with SessionLocal() as db:
            db.add(User(username="admin", password_hash=hash_password("testing-password-123"), role="admin"))
            db.commit()
        assert client.get("/health").json()["status"] == "ok"
        response = client.post("/api/v1/auth/token", data={"username": "admin", "password": "testing-password-123"})
        assert response.status_code == 200, response.text
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        product = client.post("/api/v1/products", json={"sku": "PAPER-01", "name": "Paper Bag"}, headers=headers)
        assert product.status_code == 201, product.text
        lot = client.post("/api/v1/lots", json={"lot_code": "UBE-LOT-001", "product_id": product.json()["id"]}, headers=headers)
        assert lot.status_code == 201, lot.text
        ins = client.post("/api/v1/inspections", json={"lot_code": "UBE-LOT-001"}, headers=headers)
        assert ins.status_code == 201, ins.text
        inspection_id = ins.json()["inspection_id"]
        pic = BytesIO()
        Image.new("RGB", (50, 50), (0, 0, 0)).save(pic, format="JPEG")
        ai = client.post(f"/api/v1/inspections/{inspection_id}/image", files={"file": ("bag.jpg", pic.getvalue(), "image/jpeg")}, headers=headers)
        assert ai.status_code == 200, ai.text
        assert ai.json()["ai_result"]["decision"] == "REVIEW"
        final = client.post(f"/api/v1/inspections/{inspection_id}/confirm", json={"decision": "PASS", "notes": "Checked by operator"}, headers=headers)
        assert final.status_code == 200, final.text
        history = client.get("/api/v1/lots/UBE-LOT-001/inspections", headers=headers)
        assert len(history.json()) == 1
        summary = client.get("/api/v1/dashboard/summary", headers=headers)
        assert summary.json()["passed"] == 1, summary.text
        print("PASS: Login -> product -> lot -> inspection -> image -> mock AI -> human confirmation -> dashboard")
