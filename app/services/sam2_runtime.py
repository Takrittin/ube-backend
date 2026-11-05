"""Local, cached SAM 2.1 image segmentation using the official Transformers API.

Downloads are an explicit setup step, never performed during an inspection.
Only safetensors are loaded; no remote model code is executed.
"""
from pathlib import Path
from threading import Lock

import cv2
import numpy as np

_lock = Lock()
_model = None
_processor = None


def ready(model_dir):
    path = Path(model_dir)
    return (path / "config.json").is_file() and any(path.glob("*.safetensors"))


def segment(image, selected, detections, settings):
    import torch
    from transformers import Sam2Model, Sam2Processor

    from app import config
    if not ready(config.SAM2_MODEL_DIR):
        raise RuntimeError("SAM2_MODEL_NOT_PREPARED")
    height, width = image.shape[:2]
    x1, y1, x2, y2 = selected["box"]
    padding_x, padding_y = (x2 - x1) * .1, (y2 - y1) * .1
    roi = [max(0, int(x1 - padding_x)), max(0, int(y1 - padding_y)),
           min(width, int(np.ceil(x2 + padding_x))), min(height, int(np.ceil(y2 + padding_y)))]
    ox, oy, rx, ry = roi
    crop = cv2.cvtColor(image[oy:ry, ox:rx], cv2.COLOR_BGR2RGB)
    positive = [[(x1 + x2) / 2 - ox, y1 + (y2 - y1) * f - oy] for f in (.15, .5, .85)]
    negative = []
    for detection in detections:
        if detection["class_name"] not in {"pallet", "pallet-detection", "wooden-pallet"}:
            continue
        a, b, c, d = detection["box"]
        cx, cy = (a + c) / 2, (b + d) / 2
        if ox <= cx < rx and oy <= cy < ry and not (x1 <= cx <= x2 and y1 <= cy <= y2):
            negative.append([cx - ox, cy - oy])
    global _model, _processor
    with _lock:
        if _model is None:
            torch.set_num_threads(4)
            _processor = Sam2Processor.from_pretrained(config.SAM2_MODEL_DIR, local_files_only=True)
            _model = Sam2Model.from_pretrained(config.SAM2_MODEL_DIR, local_files_only=True,
                use_safetensors=True).to(config.SAM2_DEVICE).eval()
        inputs = _processor(images=crop,
            input_boxes=[[[x1 - ox, y1 - oy, x2 - ox, y2 - oy]]],
            input_points=[[[*positive, *negative]]], input_labels=[[[*([1] * len(positive)), *([0] * len(negative))]]],
            return_tensors="pt").to(config.SAM2_DEVICE)
        with torch.inference_mode():
            output = _model(**inputs, multimask_output=False)
        masks = _processor.post_process_masks(output.pred_masks.cpu(), inputs["original_sizes"].cpu())[0]
        mask = masks[0, 0].numpy().astype(bool)
        score = float(output.iou_scores.detach().cpu().reshape(-1)[0])
    return {"mask": mask, "score": score, "roi": roi}
