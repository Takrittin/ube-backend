"""Deterministic geometry and async workflow tests; no network or model download."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import cv2
import numpy as np

TEMP = tempfile.TemporaryDirectory()
ENV = patch.dict(os.environ, {"DATABASE_URL": f"sqlite:///{TEMP.name}/test.db", "UPLOAD_DIR": f"{TEMP.name}/images",
    "JWT_SECRET_KEY": "automatic-check-tests-only-not-production-secret", "AI_PROVIDER": "mock", "AUTO_CHECK_EXPECTED_LAYERS": "8",
    "AUTO_CHECK_MAX_TILT_DEG": "3", "AUTO_CHECK_MAX_OFFSET_PERCENT": "4", "AUTO_CHECK_CAMERA_ROLL_DEG": "0"})
ENV.start()

from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal, engine
from app.models import User, Inspection, InspectionImage, AIResult, now_utc
from app.security import make_token
from app.services.automatic_check import automatic_check, alignment, find_layers, detections_from, select_stack
from app.services.roboflow import AIServiceError


def tearDownModule():
    engine.dispose()
    ENV.stop()
    TEMP.cleanup()


IMAGE = np.full((600, 500, 3), 230, np.uint8)
BLOB = cv2.imencode('.png', IMAGE)[1].tobytes()


def evidence(count=8, shift=0):
    predictions = [{"class_name": "paper-bag-stack", "confidence": .95, "x": 250, "y": 300, "width": 280, "height": 440}]
    for index in range(count):
        predictions.append({"class_name": "bag-layer", "confidence": .9, "x": 250 + (shift if index == 3 else 0),
                            "y": 105 + index * 54, "width": 260, "height": 50})
    return {"image": {"width": 500, "height": 600}, "predictions": predictions}


def mask(degrees=0, score=.95):
    canvas = np.zeros((528, 336), np.uint8)
    slope = np.tan(np.deg2rad(degrees))
    for y in range(44, 484):
        center = 168 + (y - 264) * slope
        canvas[y, int(center - 130):int(center + 130)] = 1
    return {"mask": canvas, "score": score, "roi": [82, 36, 418, 564]}


class GeometryTests(unittest.TestCase):
    def check(self, measurement=None, segmentation=None):
        return automatic_check(BLOB, measurement or evidence(), segmenter=lambda *args: segmentation or mask())

    def test_complete_evidence_pass_and_json_serializable(self):
        result = self.check()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['layer_count']['observed'], 8)
        self.assertAlmostEqual(result['tilt']['degrees'], 0, places=3)
        self.assertEqual(len(result['alignment']['pairs']), 7)
        self.assertTrue(result['criteria']['provisional'])
        json.dumps(result, allow_nan=False)

    def test_known_tilt_and_layer_edge_failures(self):
        result = self.check(segmentation=mask(5))
        self.assertEqual(result['tilt']['status'], 'FAIL')
        self.assertAlmostEqual(result['tilt']['degrees'], 5, delta=.2)
        result = self.check(evidence(shift=20))
        self.assertEqual(result['alignment']['status'], 'FAIL')
        self.assertAlmostEqual(result['alignment']['max_edge_offset_percent'], 20 / 260 * 100, places=3)
        self.assertEqual(result['status'], 'FAIL')

    def test_missing_stack_layers_and_wrong_model_never_pass(self):
        for predictions in ([], [{"class_name": "pallet", "confidence": .9, "x": 250, "y": 300, "width": 280, "height": 440}]):
            result = self.check({'predictions': predictions})
            self.assertEqual(result['status'], 'CANNOT_INSPECT')
        result = self.check(evidence(0))
        self.assertEqual(result['layer_count']['status'], 'CANNOT_INSPECT')
        self.assertEqual(result['status'], 'CANNOT_INSPECT')

    def test_bad_mask_is_review_but_known_count_failure_stays_fail(self):
        result = self.check(segmentation=mask(score=.3))
        self.assertEqual(result['status'], 'CANNOT_INSPECT')
        self.assertIsNone(result['tilt']['degrees'])
        result = self.check(evidence(7), mask(score=.3))
        self.assertEqual(result['status'], 'FAIL')
        result = self.check(segmentation=mask(score=float('nan')))
        json.dumps(result, allow_nan=False)

    def test_truncated_stack_or_coordinate_mismatch_cannot_be_assessed(self):
        data = evidence()
        data['predictions'][0]['width'] = 500
        result = self.check(data)
        self.assertEqual(result['problems'], ['STACK_TRUNCATED'])
        data = evidence(); data['image']['width'] = 1000
        self.assertEqual(self.check(data)['problems'], ['DETECTION_IMAGE_SIZE_MISMATCH'])

    def test_deduplicate_layers_and_ignore_other_stacks(self):
        data = evidence()
        data['predictions'].append(dict(data['predictions'][1], confidence=.8))
        data['predictions'].append(dict(data['predictions'][1], x=30, width=25))
        self.assertEqual(self.check(data)['layer_count']['observed'], 8)

    def test_overlap_rejected_and_camera_roll_compensated(self):
        layers = [{'box': [100, 100, 300, 200], 'index': 1}, {'box': [100, 110, 300, 210], 'index': 2}]
        self.assertEqual(alignment(layers, 0, 4)['status'], 'CANNOT_INSPECT')
        layers = [{'box': [100, 100, 300, 140], 'index': 1}, {'box': [110, 200, 310, 240], 'index': 2}]
        self.assertEqual(alignment(layers, np.degrees(np.arctan(.1)), 4)['status'], 'PASS')

    def test_sam_failure_is_safe_and_retains_detector_evidence(self):
        def fail(*args): raise RuntimeError('secret-local-path')
        result = automatic_check(BLOB, evidence(), segmenter=fail)
        self.assertEqual(result['status'], 'CANNOT_INSPECT')
        self.assertEqual(result['layer_count']['status'], 'PASS')
        self.assertNotIn('secret-local-path', json.dumps(result))


class BackgroundWorkflow(unittest.TestCase):
    def test_processing_timestamp_matches_across_database_timezones(self):
        from datetime import datetime, timezone, timedelta
        from app.routers.inspections import utc_timestamp
        utc = datetime(2026, 11, 5, 1, 0, tzinfo=timezone.utc)
        bangkok = utc.astimezone(timezone(timedelta(hours=7)))
        self.assertEqual(utc_timestamp(utc), utc_timestamp(bangkok))
        self.assertEqual(utc_timestamp(utc), utc_timestamp(utc.replace(tzinfo=None)))

    def setUp(self):
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        from uuid import uuid4
        with SessionLocal() as db:
            user = User(username=uuid4().hex, role='inspector', password_hash='unused')
            other = User(username=uuid4().hex, role='inspector', password_hash='unused')
            db.add_all([user, other]); db.commit()
            self.headers = {'Authorization': 'Bearer ' + make_token(user)}
            self.other_headers = {'Authorization': 'Bearer ' + make_token(other)}
        self.id = self.client.post('/api/v1/inspections', headers=self.headers, json={}).json()['inspection_id']
        self.path = '/api/v1/inspections/' + self.id

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def upload(self):
        return self.client.post(self.path + '/image?background=true', headers=self.headers, files={'file': ('p.png', BLOB, 'image/png')})

    def test_async_image_saved_once_and_result_preserved(self):
        result = {'decision': 'PASS', 'confidence': .9, 'measurements': {'automatic_check': automatic_check(BLOB, evidence(), segmenter=lambda *args: mask())}, 'model_version': 'offline-test', 'defect_type': None}
        with patch('app.routers.inspections.analyze_image', new=AsyncMock(return_value=result)) as analyze:
            response = self.upload()
            self.assertEqual(response.status_code, 202, response.text)
            record = self.client.get(self.path, headers=self.headers).json()
            self.assertEqual(record['status'], 'AI_PROCESSED')
            self.assertEqual(record['ai_result']['measurements'], result['measurements'])
            self.assertEqual(self.upload().status_code, 409)
            self.assertEqual(self.client.post(self.path + '/analyze', headers=self.headers).status_code, 202)
            analyze.assert_awaited_once()
        self.assertEqual(self.client.post(self.path + '/confirm', headers=self.headers, json={'decision': 'FAIL'}).status_code, 422)

    def test_failure_preserves_image_and_owner_can_retry(self):
        with patch('app.routers.inspections.analyze_image', new=AsyncMock(side_effect=AIServiceError('Safe provider message'))):
            self.assertEqual(self.upload().status_code, 202)
        record = self.client.get(self.path, headers=self.headers).json()
        self.assertEqual(record['status'], 'AI_FAILED')
        self.assertTrue(record['has_image'])
        self.assertIsNone(record['ai_result'])
        self.assertEqual(self.client.post(self.path + '/analyze', headers=self.other_headers).status_code, 403)
        result = {'decision': 'REVIEW', 'confidence': 0, 'measurements': {}, 'model_version': 'offline', 'defect_type': None}
        with patch('app.routers.inspections.analyze_image', new=AsyncMock(return_value=result)) as analyze:
            self.assertEqual(self.client.post(self.path + '/analyze', headers=self.headers).status_code, 202)
            analyze.assert_awaited_once()
        record = self.client.get(self.path, headers=self.headers).json()
        self.assertEqual(record['status'], 'AI_PROCESSED')
        self.assertIsNone(record['processing_error'])

    def test_processing_claim_prevents_duplicate_task_and_expired_attempt_cannot_commit(self):
        from datetime import timedelta
        from app.routers.inspections import process_saved_image
        import asyncio
        path = Path(TEMP.name) / 'saved.png'; path.write_bytes(BLOB)
        with SessionLocal() as db:
            item = db.get(Inspection, self.id)
            old = now_utc() - timedelta(minutes=20)
            item.status, item.processing_started_at = 'PROCESSING', now_utc()
            db.add(InspectionImage(inspection_id=self.id, stored_path=str(path))); db.commit()
        self.assertEqual(self.client.post(self.path + '/analyze', headers=self.headers).status_code, 409)
        result = {'decision': 'REVIEW', 'confidence': 0, 'measurements': {}, 'model_version': 'offline', 'defect_type': None}
        with patch('app.routers.inspections.analyze_image', new=AsyncMock(return_value=result)):
            asyncio.run(process_saved_image(self.id, old))
        self.assertEqual(self.client.get(self.path, headers=self.headers).json()['status'], 'PROCESSING')
        with SessionLocal() as db:
            item = db.get(Inspection, self.id); item.processing_started_at = old; db.commit()
        with patch('app.routers.inspections.analyze_image', new=AsyncMock(return_value=result)):
            self.assertEqual(self.client.post(self.path + '/analyze', headers=self.headers).status_code, 202)
        self.assertEqual(self.client.get(self.path, headers=self.headers).json()['status'], 'AI_PROCESSED')


if __name__ == '__main__': unittest.main()
