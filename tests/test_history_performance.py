"""Offline regression: listing history has constant query count and unchanged data."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory()
ENV = patch.dict(os.environ, {
    "DATABASE_URL": f"sqlite:///{TEMP.name}/history.db",
    "JWT_SECRET_KEY": "offline-history-test-secret",
})
ENV.start()

from sqlalchemy import event
from app.database import Base, engine, SessionLocal
from app.models import User, Product, Lot, Inspection, AIResult, FinalResult, InspectionImage, GeometryMeasurement
from app.routers.inspections import list_inspections, inspection_data


def tearDownModule():
    engine.dispose()
    ENV.stop()
    TEMP.cleanup()


class HistoryQueryTest(unittest.TestCase):
    def test_joined_history_matches_detail_with_one_query_per_page(self):
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            user = User(username="history-user", full_name="History User", role="admin", password_hash="unused")
            product = Product(sku="history-product", name="Bags")
            db.add_all([user, product]); db.flush()
            lot = Lot(lot_code="history-lot", product_id=product.id)
            db.add(lot); db.flush()
            for i in range(12):
                item = Inspection(inspector_id=user.id, lot_id=lot.id if i % 2 else None,
                                  status="CONFIRMED" if i % 2 else "DRAFT",
                                  created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
                db.add(item); db.flush()
                if i % 2:
                    db.add_all([
                        AIResult(inspection_id=item.id, decision="REVIEW", confidence=.8,
                                 measurements={"predictions": []}, model_version="test"),
                        FinalResult(inspection_id=item.id, decided_by=user.id, decision="PASS", notes="reviewed", defects=[]),
                        InspectionImage(inspection_id=item.id, stored_path="unused.jpg"),
                        GeometryMeasurement(inspection_id=item.id, measured_by=user.id, annotation={}, report={"test": True}),
                    ])
            db.commit()
        queries = []
        def count(*args):
            queries.append(args[2])
        with SessionLocal() as db:
            event.listen(engine, "before_cursor_execute", count)
            try:
                first = list_inspections(offset=0, limit=1, db=db, user=None)
                self.assertEqual(len(queries), 1)
                queries.clear()
                all_items = list_inspections(offset=0, limit=200, db=db, user=None)
                self.assertEqual(len(queries), 1)
                queries.clear()
                second = list_inspections(offset=1, limit=1, db=db, user=None)
                self.assertEqual(len(queries), 1)
            finally:
                event.remove(engine, "before_cursor_execute", count)
            self.assertEqual(len(all_items), 12)
            self.assertEqual(first, all_items[:1])
            self.assertEqual(second, all_items[1:2])
            for row in all_items:
                self.assertEqual(row, inspection_data(db, db.get(Inspection, row["inspection_id"])))
            self.assertEqual(list_inspections(offset=20, limit=1, db=db, user=None), [])
