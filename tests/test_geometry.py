"""Known-shape tests for the experimental visible-image geometry, no cloud."""
import math
import json
import unittest
import cv2
import numpy as np
from app.services.geometry import measure, suggest


def picture(blank=False):
    image = np.full((600, 800, 3), 240, dtype=np.uint8)
    if not blank:
        for y, shift in ((360, 0), (230, 100)):
            for x in (80 + shift, 300 + shift):
                cv2.rectangle(image, (x, y), (x + 200, y + 90), (20, 20, 20), 3)
    return cv2.imencode(".png", image)[1].tobytes()


def bag(x, y, width=200, height=90, angle=0, layer=1):
    return {"points": cv2.boxPoints(((x, y), (width, height), angle)).tolist(), "layer": layer}


def annotation(bags, baseline=None):
    return {"image_width": 800, "image_height": 600, "baseline": baseline or [[10, 550], [790, 550]], "bags": bags}


class GeometryTests(unittest.TestCase):
    def test_horizontal_and_signed_rotation(self):
        for angle in (0, 12, -17):
            report = measure(picture(), annotation([bag(400, 300, angle=angle)]))
            self.assertAlmostEqual(report["bags"][0]["tilt_deg"], angle, delta=.05)
            self.assertEqual(report["status"], "REVIEW")

    def test_reference_roll_is_removed_and_endpoint_order_does_not_matter(self):
        baseline = [[50, 400], [750, 400 + 700 * math.tan(math.radians(10))]]
        for reference in (baseline, baseline[::-1]):
            report = measure(picture(), annotation([bag(400, 300, angle=25)], reference))
            self.assertAlmostEqual(report["bags"][0]["tilt_deg"], 15, delta=.05)

    def test_square_has_no_invented_long_axis(self):
        report = measure(picture(), annotation([bag(400, 300, width=100, height=100)]))
        self.assertIsNone(report["bags"][0]["tilt_deg"])
        self.assertIsNone(report["bags"][0]["axis"])

    def test_aligned_and_half_offset_rows_return_raw_measurements(self):
        for offset, expected in ((0, 0), (100, .5)):
            bags = [bag(200, 400), bag(420, 400), bag(200 + offset, 250, layer=2), bag(420 + offset, 250, layer=2)]
            report = measure(picture(), annotation(bags))
            json.dumps(report, allow_nan=False)  # All values must persist as standard JSON, not NumPy scalars.
            self.assertAlmostEqual(report["layer_offsets"][0]["offset_ratio"], expected, delta=.01)
            self.assertEqual(report["status"], "REVIEW")
            self.assertNotIn("PASS", str(report))

    def test_missing_reversed_nonadjacent_and_overlapping_layers_do_not_certify_pattern(self):
        cases = [
            [bag(200, 400)],
            [bag(200, 400), bag(400, 400), bag(200, 250, layer=3), bag(400, 250, layer=3)],
            [bag(200, 250), bag(400, 250), bag(200, 400, layer=2), bag(400, 400, layer=2)],
            [bag(200, 400), bag(400, 400), bag(200, 395, layer=2), bag(400, 395, layer=2)],
        ]
        for bags in cases:
            report = measure(picture(), annotation(bags))
            self.assertEqual(report["layer_offsets"], [])
            self.assertEqual(report["pattern_status"], "insufficient_visible_layers")

    def test_bad_points_baseline_and_dimensions_are_rejected(self):
        cases = [annotation([bag(-200, 100)]), annotation([bag(200, 200)], [[20, 20], [20, 300]]),
                 annotation([{"points": [[10, 10]] * 4, "layer": 1}]),
                 {**annotation([bag(200, 200)]), "image_width": 100}]
        for data in cases:
            with self.assertRaises(ValueError):
                measure(picture(), data)

    def test_suggestions_are_bounded_unverified_and_blank_has_none(self):
        self.assertEqual(suggest(picture(blank=True))["bags"], [])
        result = suggest(picture())
        self.assertGreater(len(result["bags"]), 0)
        self.assertLessEqual(len(result["bags"]), 40)
        self.assertEqual(result["status"], "unverified_edge_proposals")
        for proposal in result["bags"]:
            for x, y in proposal["points"]:
                self.assertTrue(0 <= x <= 800 and 0 <= y <= 600)

    def test_pallet_regions_are_excluded_from_proposals(self):
        result = suggest(picture(), {"predictions": [{"is_pallet": True, "y": 100, "height": 100}]})
        self.assertEqual(result["bags"], [])


if __name__ == "__main__":
    unittest.main()
