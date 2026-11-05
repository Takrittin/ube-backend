"""Run the built Next.js proxy against FastAPI using a disposable DB and mock AI.

Build ../ube-frontend first, then: python -m tests.web_flow
No development data or cloud inference is used. Both servers stop on completion.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(url, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Test server exited before becoming ready")
        try:
            if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Test server did not become ready")


def main(database_url=None):
    backend = Path(__file__).resolve().parent.parent
    frontend = backend.parent / "ube-frontend"
    backend_port, frontend_port = free_port(), free_port()
    origin = f"http://127.0.0.1:{frontend_port}"
    with tempfile.TemporaryDirectory() as folder:
        env = {**os.environ,
            "DATABASE_URL": database_url or f"sqlite:///{folder}/web.db", "UPLOAD_DIR": f"{folder}/uploads",
            "JWT_SECRET_KEY": "web-integration-test-secret-not-for-production",
            "SEED_ADMIN_USERNAME": "web-test-admin", "SEED_ADMIN_PASSWORD": "web-test-password-123",
            "AI_PROVIDER": "mock", "ROBOFLOW_API_KEY": "", "AI_SERVICE_URL": "",
            "BACKEND_API_URL": f"http://127.0.0.1:{backend_port}/api/v1",
        }
        subprocess.run([sys.executable, "-m", "app.seed"], cwd=backend, env=env, check=True, capture_output=True)
        processes = []
        with open(Path(folder) / "servers.log", "w+") as log:
            try:
                api = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(backend_port)],
                                       cwd=backend, env=env, stdout=log, stderr=log)
                processes.append(api)
                web = subprocess.Popen(["node", "node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", str(frontend_port)],
                                       cwd=frontend, env=env, stdout=log, stderr=log)
                processes.append(web)
                wait_ready(f"http://127.0.0.1:{backend_port}/health", api)
                wait_ready(origin, web)
                with httpx.Client(base_url=origin + "/api/backend", headers={"Origin": origin}, timeout=30, trust_env=False) as c:
                    def expect(response, status=200):
                        assert response.status_code == status, (response.status_code, response.text)
                        return response
                    expect(c.get("/users/me"), 401)
                    # A different browser origin cannot log in or mutate the session.
                    expect(c.post("/auth/token", headers={"Origin": "https://other.invalid"}, data={}), 403)
                    response = expect(c.post("/auth/token", data={"username": "web-test-admin", "password": "web-test-password-123"}))
                    assert "HttpOnly" in response.headers["set-cookie"]
                    assert "access_token" not in response.text
                    assert expect(c.get("/users/me")).json()["role"] == "admin"
                    expect(c.get("/products"))
                    product = expect(c.post("/products", json={"sku": "WEB-01", "name": "Web test bag", "bag_type": "paper"}), 201).json()
                    lot = expect(c.post("/lots", json={"lot_code": "WEB-LOT", "product_id": product["id"]}), 201).json()
                    record = expect(c.post("/inspections", json={"lot_code": lot["lot_code"], "pallet_code": "P1", "material_code": "M1"}), 201).json()
                    identifier = record["inspection_id"]
                    blob = BytesIO()
                    Image.new("RGB", (40, 40), "white").save(blob, "JPEG")
                    analyzed = expect(c.post(f"/inspections/{identifier}/image", files={"file": ("test.jpg", blob.getvalue(), "image/jpeg")})).json()
                    assert analyzed["ai_result"]["decision"] == "REVIEW"
                    geometry = expect(c.post(f"/inspections/{identifier}/geometry", json={
                        "image_width": 40, "image_height": 40, "baseline": [[2, 35], [38, 35]],
                        "bags": [{"points": [[3, 3], [25, 3], [25, 15], [3, 15]], "layer": 1}], "verified": True,
                    })).json()
                    assert geometry["report"]["bags"][0]["tilt_deg"] == 0
                    expect(c.post(f"/inspections/{identifier}/confirm", json={"decision": "PASS", "notes": "Manually checked"}))
                    assert expect(c.get(f"/inspections/{identifier}/image")).headers["content-type"].startswith("image/")
                    history = expect(c.get("/inspections?limit=200&offset=0")).json()
                    assert history[0]["final_result"]["decision"] == "PASS"
                    assert history[0]["pallet_code"] == "P1"
                    assert history[0]["geometry_result"]["report"]["status"] == "REVIEW"
                    assert expect(c.get("/dashboard/summary")).json()["passed"] == 1
                    unassigned = expect(c.post("/inspections", json={}), 201).json()["inspection_id"]
                    submitted = expect(c.post(f"/inspections/{unassigned}/image?background=true", files={"file": ("test.jpg", blob.getvalue(), "image/jpeg")}), 202).json()
                    assert submitted["status"] == "PROCESSING"
                    deadline = time.monotonic() + 15
                    while True:
                        processed = expect(c.get(f"/inspections/{unassigned}")).json()
                        if processed["status"] != "PROCESSING": break
                        assert time.monotonic() < deadline, "Background analysis did not finish"
                        time.sleep(.1)
                    assert processed["status"] == "AI_PROCESSED", processed
                    assert processed["processing_started_at"] and processed["processing_error"] is None
                    expect(c.post(f"/inspections/{unassigned}/analyze"), 202)  # Completed jobs do not run inference again.
                    expect(c.post(f"/inspections/{unassigned}/confirm", json={"decision": "PASS", "notes": "Checked without lot", "defects": ["ถุงสินค้าเสียหาย"]}))
                    saved = expect(c.get(f"/inspections/{unassigned}")).json()
                    assert saved["lot_id"] is None and saved["lot_code"] is None
                    assert saved["status"] == "CONFIRMED"
                    assert saved["final_result"]["defects"] == ["ถุงสินค้าเสียหาย"]
                    user = expect(c.post("/users", json={"username": "web-inspector", "password": "web-test-password-123", "role": "inspector"}), 201).json()
                    expect(c.patch(f"/users/{user['id']}", json={"role": "qa"}))
                    expect(c.delete(f"/users/{user['id']}"), 204)
                    expect(c.post("/auth/logout"))
                    expect(c.get("/users/me"), 401)
                    expect(c.get(f"/inspections/{identifier}/image"), 401)
                print("PASS: Next.js HttpOnly login -> FastAPI -> DB -> upload -> AI REVIEW -> confirmation -> history/image -> user roles -> logout; cross-origin writes blocked")
            except Exception:
                log.flush(); log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                for process in reversed(processes):
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()


if __name__ == "__main__":
    main()
