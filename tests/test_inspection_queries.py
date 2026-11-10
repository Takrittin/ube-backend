"""Offline pagination and database-aggregate regressions; no cloud calls."""
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory()
ENV = patch.dict(os.environ, {
    "DATABASE_URL": f"sqlite:///{TEMP.name}/queries.db",
    "UPLOAD_DIR": f"{TEMP.name}/images",
    "JWT_SECRET_KEY": "offline-query-test-secret",
    "AI_PROVIDER": "mock",
})
ENV.start()

from fastapi.testclient import TestClient
from sqlalchemy import event
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import AIResult, FinalResult, Inspection, Lot, Product, User
from app.security import make_token
from app.services.inspection_queries import dashboard_analytics, inspection_page


def tearDownModule():
    engine.dispose()
    ENV.stop()
    TEMP.cleanup()


class InspectionQueriesTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            self.user = User(id="query-user", username="query-user", full_name="Quality Inspector",
                             role="inspector", password_hash="unused")
            self.product = Product(id="query-product", sku="Q-01", name="Paper Bags")
            self.lot = Lot(id="query-lot", product_id=self.product.id, lot_code="LOT_A")
            db.add_all([self.user, self.product, self.lot])
            db.commit()

    def add(self, db, identifier, created="2026-11-09T00:00:00+00:00", *,
            status="DRAFT", material="M1", ai=None, final=None, defects=(), pallet="P001", lot=True):
        item = Inspection(id=identifier, inspector_id=self.user.id, lot_id=self.lot.id if lot else None,
                          created_at=datetime.fromisoformat(created), status=status, material_code=material,
                          pallet_code=pallet, qr_raw="QR|MATERIAL")
        db.add(item)
        if ai:
            db.add(AIResult(inspection_id=identifier, decision=ai, confidence=.8,
                            measurements={"predictions": []}, model_version="test"))
        if final:
            db.add(FinalResult(inspection_id=identifier, decided_by=self.user.id,
                               decision=final, defects=list(defects)))
        return item

    def seed_dashboard(self):
        with SessionLocal() as db:
            self.add(db, "previous", "2026-11-08T16:59:59+00:00", status="CONFIRMED", ai="FAIL", final="FAIL")
            self.add(db, "a", "2026-11-08T17:00:00+00:00", status="CONFIRMED", ai="PASS", final="PASS")
            self.add(db, "b", status="CONFIRMED", ai="PASS", final="FAIL", defects=["torn", "torn"])
            self.add(db, "c", "2026-11-09T16:59:59+00:00", status="AI_PROCESSED", ai="REVIEW")
            self.add(db, "d", status="CONFIRMED", material="M2", ai="REVIEW", final="PASS", defects=["torn", "wrinkle"], lot=False)
            self.add(db, "e")
            self.add(db, "next", "2026-11-09T17:00:00+00:00", material="M3")
            db.commit()

    def test_history_pages_are_bounded_and_use_constant_query_count(self):
        with SessionLocal() as db:
            for index in range(251):
                self.add(db, f"record-{index:03}")
            db.commit()
        queries = []
        def count(*args):
            queries.append(args[2])
        with SessionLocal() as db:
            event.listen(engine, "before_cursor_execute", count)
            try:
                first = inspection_page(db, limit=50)
                self.assertEqual(len(queries), 2)
                queries.clear()
                last = inspection_page(db, offset=250, limit=50)
                self.assertEqual(len(queries), 2)
                queries.clear()
                empty = inspection_page(db, offset=300, limit=50)
                self.assertEqual(len(queries), 2)
            finally:
                event.remove(engine, "before_cursor_execute", count)
        self.assertEqual(first["total"], 251)
        self.assertEqual(len(first["items"]), 50)
        self.assertEqual(first["items"][0]["inspection_id"], "record-000")
        self.assertEqual([row["inspection_id"] for row in last["items"]], ["record-250"])
        self.assertEqual(empty["items"], [])
        self.assertEqual(last["total"], 251)

    def test_filters_combine_and_use_bangkok_calendar_boundaries(self):
        self.seed_dashboard()
        with SessionLocal() as db:
            page = inspection_page(db, date_from=date(2026, 11, 9), date_to=date(2026, 11, 9),
                                   material="M1", q="lot_a", decision="pass", search="paper bags")
            self.assertEqual([row["inspection_id"] for row in page["items"]], ["a"])
            review = inspection_page(db, decision="review")
            self.assertEqual([row["inspection_id"] for row in review["items"]], ["c", "d"])
            self.assertEqual(inspection_page(db, search="quality inspector")["total"], 7)
            self.assertEqual(inspection_page(db, search="QR|MATERIAL")["total"], 7)
            self.assertEqual(inspection_page(db, search="%LOT%")["total"], 0)
            # '_' is literal, not SQL's single-character wildcard.
            self.assertEqual(inspection_page(db, q="LOT_")["total"], 6)
            self.assertEqual(inspection_page(db, q="LOTX")["total"], 0)

    def test_dashboard_counts_all_records_and_preserves_rate_denominators(self):
        self.seed_dashboard()
        with SessionLocal() as db:
            result = dashboard_analytics(db, date(2026, 11, 9), date(2026, 11, 9))
        self.assertEqual(result["inspected"], 5)
        self.assertEqual(result["previousInspected"], 1)
        self.assertEqual(result["currentPass"], {"count": 2, "total": 3, "rate": 2 / 3 * 100})
        self.assertEqual(result["previousPass"], {"count": 0, "total": 1, "rate": 0})
        self.assertEqual(result["agreement"], {"count": 1, "total": 2, "rate": 50})
        self.assertEqual(result["overrides"], 1)
        self.assertEqual(result["defects"], [{"name": "torn", "count": 2}, {"name": "wrinkle", "count": 1}])
        self.assertEqual(result["materials"], [
            {"code": "M1", "inspected": 4, "count": 1, "total": 2, "rate": 50},
            {"code": "M2", "inspected": 1, "count": 1, "total": 1, "rate": 100},
        ])
        self.assertEqual(result["materialOptions"], ["M1", "M2", "M3"])
        self.assertEqual(result["previousRange"], {"start": "2026-11-08", "end": "2026-11-08"})
        self.assertEqual(len(result["latest"]), 5)
        self.assertEqual(result["checks"]["wrap"], {"count": 0, "total": 0, "rate": None, "review": 0})

    def test_dashboard_filters_current_and_previous_cohorts_equally(self):
        self.seed_dashboard()
        with SessionLocal() as db:
            result = dashboard_analytics(db, date(2026, 11, 9), date(2026, 11, 9), material="M1", q="lot_a")
            empty = dashboard_analytics(db, date(2020, 1, 1), date(2020, 1, 1))
        self.assertEqual(result["inspected"], 4)
        self.assertEqual(result["previousInspected"], 1)
        self.assertEqual(result["currentPass"]["rate"], 50)
        self.assertEqual(result["defects"], [{"name": "torn", "count": 1}])
        self.assertEqual(empty["inspected"], 0)
        self.assertIsNone(empty["currentPass"]["rate"])
        self.assertIsNone(empty["delta"])
        self.assertEqual(empty["latest"], [])

    def test_dashboard_latest_is_bounded_and_aggregates_are_not(self):
        with SessionLocal() as db:
            for index in range(251):
                self.add(db, f"record-{index:03}", status="CONFIRMED", ai="PASS", final="PASS")
            db.commit()
        queries = []
        def count(*args):
            queries.append(args[2])
        with SessionLocal() as db:
            event.listen(engine, "before_cursor_execute", count)
            try:
                result = dashboard_analytics(db, date(2026, 11, 9), date(2026, 11, 9))
            finally:
                event.remove(engine, "before_cursor_execute", count)
        self.assertEqual(result["inspected"], 251)
        self.assertEqual(result["currentPass"]["total"], 251)
        self.assertEqual(len(result["latest"]), 10)
        self.assertEqual(len(queries), 6)

    def test_dashboard_endpoint_requires_login_and_allows_inspector(self):
        with TestClient(app) as client:
            path = "/api/v1/dashboard/analytics?date_from=2026-11-09&date_to=2026-11-09"
            self.assertEqual(client.get(path).status_code, 401)
            headers = {"Authorization": "Bearer " + make_token(self.user)}
            response = client.get(path, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            invalid = client.get("/api/v1/dashboard/analytics?date_from=2026-11-10&date_to=2026-11-09", headers=headers)
            self.assertEqual(invalid.status_code, 422)

    def test_page_endpoint_validates_pagination_and_filters_before_inspection_id_route(self):
        self.seed_dashboard()
        with TestClient(app) as client:
            path = "/api/v1/inspections/page"
            self.assertEqual(client.get(path).status_code, 401)
            headers = {"Authorization": "Bearer " + make_token(self.user)}
            response = client.get(path, headers=headers, params={"offset": 1, "limit": 1,
                "date_from": "2026-11-09", "date_to": "2026-11-09", "material": "M1", "decision": "all"})
            self.assertEqual(response.status_code, 200, response.text)
            page = response.json()
            self.assertEqual(page["total"], 4)
            self.assertEqual(page["offset"], 1)
            self.assertEqual(len(page["items"]), 1)
            for params in ({"limit": 201}, {"offset": -1}, {"decision": "invalid"}, {"date_from": "not-a-date"}):
                self.assertEqual(client.get(path, headers=headers, params=params).status_code, 422)


if __name__ == "__main__":
    unittest.main()
