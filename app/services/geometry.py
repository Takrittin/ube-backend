"""Experimental 2D measurements from operator-verified visible bag outlines.

No quality thresholds or physical/3D angles are inferred. Edge proposals are
unverified image regions, not bag detections. All coordinates use stored pixels.
"""
import math
from statistics import median

import cv2
import numpy as np

VERSION = "opencv-visible-geometry-v1"


def decode_image(blob):
    image = cv2.imdecode(np.frombuffer(blob, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Cannot decode stored image")
    return image


def point(value, width, height):
    x, y = float(value[0]), float(value[1])
    if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x <= width and 0 <= y <= height):
        raise ValueError("All annotation points must be inside the image")
    return [x, y]


def angle180(angle):
    return (angle + 90) % 180 - 90


def measure(blob, annotation):
    image = decode_image(blob)
    height, width = image.shape[:2]
    if annotation["image_width"] != width or annotation["image_height"] != height:
        raise ValueError("Image dimensions changed; reload and annotate the stored image")
    baseline = [point(p, width, height) for p in annotation["baseline"]]
    a, b = baseline
    if a[0] > b[0]:
        a, b = b, a
    dx, dy = b[0] - a[0], b[1] - a[1]
    if math.hypot(dx, dy) < 10 or abs(dy) > dx:
        raise ValueError("Mark a clear left-to-right horizontal reference, at least 10 pixels long")
    reference = math.degrees(math.atan2(dy, dx))
    radians = math.radians(reference)
    cosine, sine = math.cos(radians), math.sin(radians)

    def project(p):
        x, y = float(p[0]), float(p[1])
        return [x * cosine + y * sine, -x * sine + y * cosine]

    bags, layers = [], {}
    for index, bag in enumerate(annotation["bags"]):
        corners = np.array([point(p, width, height) for p in bag["points"]], dtype=np.float32)
        hull = cv2.convexHull(corners).reshape(-1, 2)
        if len(hull) != 4 or cv2.contourArea(hull) < 25:
            raise ValueError("Each bag needs four distinct corners enclosing a visible area")
        rectangle = cv2.boxPoints(cv2.minAreaRect(hull))
        edges = [(rectangle[(i + 1) % 4] - rectangle[i]) for i in range(4)]
        lengths = [float(np.linalg.norm(edge)) for edge in edges]
        longest = int(np.argmax(lengths))
        vector = edges[longest]
        center = rectangle.mean(axis=0)
        ratio = max(lengths) / max(min(lengths), 1e-9)
        # Near-square outlines have no stable long axis. This is an ambiguity
        # guard, not an acceptable tilt or bag quality tolerance.
        stable_axis = ratio >= 1.15
        tilt = angle180(math.degrees(math.atan2(float(vector[1]), float(vector[0]))) - reference) if stable_axis else None
        axis = [[float(v) for v in center - vector / 2], [float(v) for v in center + vector / 2]] if stable_axis else None
        projected = np.array([project(p) for p in hull])
        center_projected = project(center)
        result = {"id": index + 1, "layer": bag["layer"], "outline": hull.tolist(),
                  "center": center.tolist(), "axis": axis,
                  "tilt_deg": round(tilt, 3) if tilt is not None else None,
                  "status": "measured" if stable_axis else "ambiguous_axis"}
        bags.append(result)
        layers.setdefault(bag["layer"], []).append({"center": center_projected,
            "width": float(np.ptp(projected[:, 0])), "height": float(np.ptp(projected[:, 1]))})
    offsets = []
    keys = sorted(layers)
    for lower_id, upper_id in zip(keys, keys[1:]):
        lower, upper = layers[lower_id], layers[upper_id]
        if upper_id != lower_id + 1 or len(lower) < 2 or len(upper) < 2:
            continue
        lower_y = median(p["center"][1] for p in lower)
        upper_y = median(p["center"][1] for p in upper)
        # Layer 1 is the bottom. Reject overlapping rows and reversed layer IDs.
        minimum_separation = .25 * median(p["height"] for p in lower + upper)
        if lower_y - upper_y <= minimum_separation:
            continue
        widths = [p["width"] for p in lower + upper]
        if min(widths) <= 0 or max(widths) / min(widths) > 1.5:
            continue  # Mixed orientations/sizes cannot share a phase estimate.
        distances = [min(abs(p["center"][0] - q["center"][0]) for q in lower) for p in upper]
        offsets.append({"lower_layer": lower_id, "upper_layer": upper_id,
            "offset_px": round(median(distances), 3),
            "offset_ratio": round(median(distances) / median(widths), 4)})
    lean = None
    if len(keys) >= 2:
        lower, upper = layers[keys[0]], layers[keys[-1]]
        cx1, cy1 = (median(p["center"][axis] for p in lower) for axis in (0, 1))
        cx2, cy2 = (median(p["center"][axis] for p in upper) for axis in (0, 1))
        if cy1 - cy2 > 10:
            lean = round(math.degrees(math.atan2(cx2 - cx1, cy1 - cy2)), 3)
    return {"version": VERSION, "opencv_version": cv2.__version__, "status": "REVIEW",
        "scope": "operator_verified_2d_visible_outlines", "image": {"width": width, "height": height},
        "reference_angle_deg": round(reference, 3), "baseline": [a, b], "bags": bags,
        "visible_stack_centerline_deg": lean, "layer_offsets": offsets,
        "pattern_status": "measured_visible_offsets" if offsets else "insufficient_visible_layers",
        "warnings": ["Camera perspective is not calibrated. Angles are apparent 2D angles relative to the marked reference.",
                     "Layer offsets include visible center displacement and do not certify hidden interlocking or a product-specific stacking pattern.",
                     "No UBE pass/fail tolerance is configured. Human review is required."]}


def suggest(blob, measurements=None):
    image = decode_image(blob)
    height, width = image.shape[:2]
    scale = min(1.0, 1280 / max(width, height))
    small = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    image_area = small.shape[0] * small.shape[1]
    for contour in contours:
        area = cv2.contourArea(contour)
        if not .002 * image_area <= area <= .18 * image_area:
            continue
        polygon = cv2.approxPolyDP(contour, .025 * cv2.arcLength(contour, True), True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        rectangle = cv2.minAreaRect(polygon)
        rw, rh = rectangle[1]
        if min(rw, rh) <= 0 or max(rw, rh) / min(rw, rh) > 6 or area / (rw * rh) < .7:
            continue
        hull = polygon.reshape(-1, 2).astype(np.float32) / scale
        # Exclude the detected pallet itself and regions below its top.
        predictions = (measurements or {}).get("predictions", [])
        pallet_tops = [p["y"] - p["height"] / 2 for p in predictions
                       if p.get("is_pallet") is True and isinstance(p.get("y"), (float, int))
                       and isinstance(p.get("height"), (float, int))]
        if pallet_tops and float(hull[:, 1].mean()) >= min(pallet_tops):
            continue
        if any(cv2.intersectConvexConvex(hull, old)[0] / min(cv2.contourArea(hull), cv2.contourArea(old)) > .6
               for old in candidates):
            continue
        candidates.append(hull)
        if len(candidates) >= 40:
            break
    # Proposed layer IDs follow vertical position, bottom first, and must be
    # verified by the operator. No confidence or semantic bag label is invented.
    candidates.sort(key=lambda p: -float(p[:, 1].mean()))
    rows = []
    result = []
    for hull in candidates:
        center_y = float(hull[:, 1].mean())
        row = next((i for i, y in enumerate(rows) if abs(center_y - y) < .45 * float(np.ptp(hull[:, 1]))), None)
        if row is None:
            rows.append(center_y)
            row = len(rows) - 1
        result.append({"points": hull.tolist(), "layer": row + 1})
    return {"image": {"width": width, "height": height}, "bags": result,
            "status": "unverified_edge_proposals", "version": VERSION}
