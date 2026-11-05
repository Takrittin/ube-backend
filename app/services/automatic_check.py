"""Automatic visible-stack checks adapted from the supplied Colab reference.

These provisional image-space checks cannot establish hidden bag interlocking,
physical stability or camera calibration. Missing evidence never becomes PASS.
"""
import logging
import math
import json
from pathlib import Path

import cv2
import numpy as np

from app import config
from app.services.sam2_runtime import segment

logger = logging.getLogger(__name__)
VERSION = "automatic-stack-v1"


def area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def intersection(a, b):
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def detections_from(measurements, width, height):
    result = []
    for p in measurements.get("predictions", []):
        try:
            x, y, w, h, confidence = [float(p[k]) for k in ("x", "y", "width", "height", "confidence")]
            if not all(math.isfinite(v) for v in (x, y, w, h, confidence)) or w <= 0 or h <= 0:
                continue
            box = [max(0., x - w / 2), max(0., y - h / 2), min(float(width), x + w / 2), min(float(height), y + h / 2)]
            if area(box) <= 0:
                continue
            result.append({"class_name": str(p.get("class_name", p.get("class", ""))).lower().replace("_", "-").replace(" ", "-"),
                           "box": box, "confidence": confidence})
        except (KeyError, TypeError, ValueError):
            continue
    return result


def select_stack(detections, width, height):
    candidates = []
    for d in detections:
        b = d["box"]
        cx, cy = (b[0] + b[2]) / 2 / width, (b[1] + b[3]) / 2 / height
        if d["class_name"] not in {"paper-bag-stack", "paperbagstack"} or d["confidence"] < .1:
            continue
        if area(b) / (width * height) < .02 or not (.1 <= cx <= .9 and .15 <= cy <= 1):
            continue
        distance = math.hypot(cx - .5, cy - .575)
        candidates.append((area(b) * (.5 + d["confidence"]) / (1 + 4 * distance), d))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def find_layers(detections, selected):
    stack = selected["box"]
    layers = []
    for d in sorted(detections, key=lambda item: item["confidence"], reverse=True):
        b = d["box"]
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if d["class_name"] not in {"bag-layer", "baglayer"} or d["confidence"] < .25:
            continue
        if not (stack[0] <= cx <= stack[2] and stack[1] <= cy <= stack[3]) or intersection(b, stack) / area(b) < .8:
            continue
        if any(intersection(b, old["box"]) / (area(b) + area(old["box"]) - intersection(b, old["box"])) > .5 for old in layers):
            continue
        layers.append(dict(d))
    layers.sort(key=lambda item: (item["box"][1] + item["box"][3]) / 2)
    for index, layer in enumerate(layers):
        layer.update(index=index + 1, status="CANNOT_INSPECT")
    return layers


def alignment(layers, roll, limit):
    if len(layers) < 2:
        return {"status": "CANNOT_INSPECT", "pairs": [], "max_center_offset_percent": None, "max_edge_offset_percent": None}
    theta = math.radians(roll)
    cos, sin = math.cos(theta), math.sin(theta)
    median_width = float(np.median([d["box"][2] - d["box"][0] for d in layers])) * cos
    pairs = []
    for first, second in zip(layers, layers[1:]):
        a, b = first["box"], second["box"]
        dy = (b[1] + b[3] - a[1] - a[3]) / 2
        overlap = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        valid = dy > 0 and overlap <= .5 * min(a[3] - a[1], b[3] - b[1])
        offset = lambda dx: round(abs(dx * cos - dy * sin) / median_width * 100, 4)
        center = offset((b[0] + b[2] - a[0] - a[2]) / 2) if valid else None
        left, right = (offset(b[0] - a[0]), offset(b[2] - a[2])) if valid else (None, None)
        status = "CANNOT_INSPECT" if not valid else "FAIL" if max(center, left, right) > limit else "PASS"
        pairs.append({"from_layer": first["index"], "to_layer": second["index"], "center_offset_percent": center,
                      "left_offset_percent": left, "right_offset_percent": right, "status": status})
    for layer in layers:
        statuses = [p["status"] for p in pairs if layer["index"] in (p["from_layer"], p["to_layer"])]
        layer["status"] = combine(statuses)
    measured = [p for p in pairs if p["center_offset_percent"] is not None]
    return {"status": combine([p["status"] for p in pairs]), "pairs": pairs,
            "max_center_offset_percent": max((p["center_offset_percent"] for p in measured), default=None),
            "max_edge_offset_percent": max((max(p["left_offset_percent"], p["right_offset_percent"]) for p in measured), default=None)}


def combine(statuses):
    return "FAIL" if "FAIL" in statuses else "CANNOT_INSPECT" if not statuses or "CANNOT_INSPECT" in statuses else "PASS"


def mask_tilt(segmentation, selected, roll, limit):
    mask = np.asarray(segmentation["mask"], dtype=np.uint8)
    ox, oy, rx, ry = segmentation["roi"]
    b = selected["box"]
    score = float(segmentation["score"])
    result = {"status": "CANNOT_INSPECT", "degrees": None, "mask_score": score if math.isfinite(score) else None, "issues": [], "outline": [], "centerline": []}
    if mask.shape != (ry - oy, rx - ox) or not mask.any():
        result["issues"] = ["EMPTY_OR_INVALID_MASK"]
        return result
    ys, xs = np.nonzero(mask)
    x, y = xs + ox, ys + oy
    width, height = b[2] - b[0], b[3] - b[1]
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    inside = (x >= b[0] - .1 * width) & (x <= b[2] + .1 * width) & (y >= b[1] - .1 * height) & (y <= b[3] + .1 * height)
    gates = {"LOW_MASK_SCORE": math.isfinite(score) and score >= .7,
             "INSUFFICIENT_MASK_AREA": len(x) / area(b) >= .6,
             "INSUFFICIENT_WIDTH_COVERAGE": (x.max() - x.min() + 1) / width >= .85,
             "INSUFFICIENT_HEIGHT_COVERAGE": (y.max() - y.min() + 1) / height >= .9,
             "MASK_OUTSIDE_STACK": float(inside.mean()) >= .9,
             "DISCONNECTED_MASK": len(stats) > 1 and int(stats[1:, cv2.CC_STAT_AREA].max()) / len(x) >= .95,
             "MASK_TOUCHES_CROP_EDGE": not (mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())}
    result["issues"] = [key for key, passed in gates.items() if not passed]
    if result["issues"]:
        return result
    bands = []
    for start, end in zip(np.linspace(y.min() + .05 * np.ptp(y), y.max() - .05 * np.ptp(y), 25)[:-1],
                          np.linspace(y.min() + .05 * np.ptp(y), y.max() - .05 * np.ptp(y), 25)[1:]):
        rows = []
        for row in range(int(math.ceil(start)), int(math.ceil(end))):
            cols = np.flatnonzero(mask[row - oy])
            if len(cols):
                rows.append((float(cols.min() + ox), float(cols.max() + ox)))
        if rows:
            left, right = np.median(np.array(rows), axis=0)
            bands.append([float((start + end) / 2), float(left), float(right)])
    median_width = float(np.median([v[2] - v[1] for v in bands])) if bands else 0
    bands = [v for v in bands if v[2] - v[1] >= .5 * median_width]
    if len(bands) < 20:
        result["issues"] = ["INSUFFICIENT_OUTLINE_BANDS"]
        return result
    slope, intercept = np.polyfit([v[0] for v in bands], [(v[1] + v[2]) / 2 for v in bands], 1)
    degrees = round(math.degrees(math.atan(float(slope))) - roll, 4)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea)
    outline = cv2.approxPolyDP(contour, .002 * cv2.arcLength(contour, True), True).reshape(-1, 2)
    result.update(status="PASS" if abs(degrees) <= limit else "FAIL", degrees=degrees,
                  outline=[[int(a + ox), int(c + oy)] for a, c in outline],
                  centerline=[[float(slope * yy + intercept), yy] for yy in (bands[0][0], bands[-1][0])])
    return result


def automatic_check(blob, measurements, segmenter=None):
    image = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Invalid inspection image")
    height, width = image.shape[:2]
    criteria = {"expected_layers": config.AUTO_CHECK_EXPECTED_LAYERS, "max_tilt_deg": config.AUTO_CHECK_MAX_TILT_DEG,
                "max_offset_percent": config.AUTO_CHECK_MAX_OFFSET_PERCENT, "camera_roll_deg": config.AUTO_CHECK_CAMERA_ROLL_DEG,
                "provisional": True}
    report = {"version": VERSION, "scope": "visible_layer_boxes_and_whole_stack_outline", "criteria": criteria,
              "image": {"width": width, "height": height}, "status": "CANNOT_INSPECT", "selected_stack": None,
              "layers": [], "layer_count": {"status": "CANNOT_INSPECT", "observed": None},
              "alignment": {"status": "CANNOT_INSPECT", "pairs": [], "max_center_offset_percent": None, "max_edge_offset_percent": None},
              "tilt": {"status": "CANNOT_INSPECT", "degrees": None, "issues": [], "outline": [], "centerline": []}, "problems": []}
    source = Path(config.SAM2_MODEL_DIR) / "ube-model-source.json"
    report["segmentation_model"] = {"repo_id": config.SAM2_MODEL_ID, "revision": None}
    if source.is_file():
        try:
            report["segmentation_model"]["revision"] = json.loads(source.read_text()).get("revision")
        except (ValueError, OSError):
            pass
    size = measurements.get("image", {})
    if size.get("width", width) != width or size.get("height", height) != height:
        report["problems"] = ["DETECTION_IMAGE_SIZE_MISMATCH"]
        return report
    detections = detections_from(measurements, width, height)
    selected = select_stack(detections, width, height)
    if selected is None:
        report["problems"] = ["STACK_NOT_DETECTED"]
        return report
    report["selected_stack"] = selected
    b = selected["box"]
    if b[0] <= 1 or b[1] <= 1 or b[2] >= width - 1 or b[3] >= height - 1:
        report["problems"] = ["STACK_TRUNCATED"]
        return report
    layers = find_layers(detections, selected)
    report["layers"] = layers
    report["layer_count"] = {"status": "CANNOT_INSPECT" if not layers else "PASS" if len(layers) == criteria["expected_layers"] else "FAIL", "observed": len(layers)}
    report["alignment"] = alignment(layers, criteria["camera_roll_deg"], criteria["max_offset_percent"])
    try:
        segmentation = (segmenter or segment)(image, selected, detections, criteria)
        report["tilt"] = mask_tilt(segmentation, selected, criteria["camera_roll_deg"], criteria["max_tilt_deg"])
    except Exception as error:
        # Never expose provider messages, local paths or tokens in persisted/UI output.
        logger.warning("Automatic segmentation unavailable (%s)", type(error).__name__)
        report["tilt"]["issues"] = ["SAM2_UNAVAILABLE"]
    statuses = [report[key]["status"] for key in ("tilt", "alignment", "layer_count")]
    report["status"] = combine(statuses)
    report["problems"] = (["LAYER_COUNT_MISMATCH"] if report["layer_count"]["status"] == "FAIL" else []) + \
        (["LAYER_MISALIGNMENT"] if report["alignment"]["status"] == "FAIL" else []) + \
        (["STACK_TILTED"] if report["tilt"]["status"] == "FAIL" else []) + \
        (["LAYERS_NOT_DETECTED"] if not layers else []) + report["tilt"]["issues"]
    if len(layers) >= 2 and report["alignment"]["status"] == "CANNOT_INSPECT":
        report["problems"].append("LAYER_ALIGNMENT_UNAVAILABLE")
    return report
