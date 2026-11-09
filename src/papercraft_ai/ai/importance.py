"""Bounded, category-independent detail proposals; importance is a heuristic, not certainty.

Florence supplies captioned regions, SAM supplies object support, and local contrast/
corners supply small structural candidates. No species-specific prompts or coordinates.
"""

import time

import cv2
import numpy as np

VERSION = "importance-1"


def saliency(image):
    lab = cv2.cvtColor(image.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    fine = cv2.GaussianBlur(lab, (0, 0), 1)
    broad = cv2.GaussianBlur(lab, (0, 0), max(3, min(image.shape[:2]) / 24))
    contrast = np.linalg.norm(fine - broad, axis=2)
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    corners = cv2.cornerMinEigenVal(gray, 5)
    corners /= max(float(corners.max()), 1e-6)
    return (
        np.clip(contrast / max(float(np.percentile(contrast, 98)), 1), 0, 1) * 0.7 + corners * 0.3
    )


def crop_windows(image, count):
    """Select nonidentical high-saliency half-image crops, in normalized fitted coordinates."""
    score = saliency(image)
    h, w = score.shape
    candidates = []
    for y in (0, 0.25, 0.5):
        for x in (0, 0.25, 0.5):
            value = score[int(y * h) : int((y + 0.5) * h), int(x * w) : int((x + 0.5) * w)].mean()
            candidates.append((float(value), (x, y, x + 0.5, y + 0.5)))
    selected = []
    for _, box in sorted(candidates, reverse=True):
        if all(overlap(box, other) < 0.35 for other in selected):
            selected.append(box)
        if len(selected) >= count:
            break
    return selected if count else []


def overlap(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    return intersection / max((a[2] - a[0]) * (a[3] - a[1]), 1e-9)


def select_details(image, semantic_regions=(), objects=None, maximum=12):
    """Select compact contrast-bearing ROIs, cap union coverage at 18 percent.

    Region captions are evidence of grounding only, not a probability of importance.
    SAM masks bias object interiors without protecting every mask or all texture.
    """
    score = saliency(image)
    h, w = score.shape
    # A spatial exclusion radius keeps a textured object from taking every slot.
    radius = max(3, round(min(h, w) * 0.045))
    candidates = []
    working = score.copy()
    for _ in range(64):
        y, x = np.unravel_index(working.argmax(), working.shape)
        if working[y, x] < 0.20:
            break
        x0, y0, x1, y1 = (
            max(0, x - radius),
            max(0, y - radius),
            min(w, x + radius + 1),
            min(h, y + radius + 1),
        )
        box = (x0 / w, y0 / h, x1 / w, y1 / h)
        region = next(
            (
                r
                for r in semantic_regions
                if r["box"][0] <= x / w <= r["box"][2] and r["box"][1] <= y / h <= r["box"][3]
            ),
            None,
        )
        supported = objects is not None and objects[y, x] > 0
        value = float(working[y, x]) * (1.35 if region else 1.15 if supported else 1)
        candidates.append(
            {
                "box": list(box),
                "score": round(value, 5),
                "label": region["label"] if region else "local structure",
                "source": "model+structure" if region else "structure",
                "semantic_grounded": region is not None,
            }
        )
        cv2.circle(working, (int(x), int(y)), radius * 3, 0, -1)
    # Compact captioned regions can themselves be useful details.
    for region in semantic_regions:
        x0, y0, x1, y1 = region["box"]
        area = (x1 - x0) * (y1 - y0)
        if not 0.001 <= area <= 0.035:
            continue
        local = score[
            int(y0 * h) : max(int(y0 * h) + 1, int(y1 * h)),
            int(x0 * w) : max(int(x0 * w) + 1, int(x1 * w)),
        ]
        if local.size and local.max() >= 0.2:
            candidates.append(
                {
                    **region,
                    "score": float(local.max()) * 1.4,
                    "source": "model+structure",
                    "semantic_grounded": True,
                }
            )
    selected, coverage = [], np.zeros((h, w), bool)
    for item in sorted(candidates, key=lambda c: c["score"], reverse=True):
        if any(
            max(overlap(item["box"], r["box"]), overlap(r["box"], item["box"])) > 0.35
            for r in selected
        ):
            continue
        x0, y0, x1, y1 = item["box"]
        proposed = coverage.copy()
        proposed[
            int(y0 * h) : max(int(y0 * h) + 1, int(np.ceil(y1 * h))),
            int(x0 * w) : max(int(x0 * w) + 1, int(np.ceil(x1 * w))),
        ] = True
        if proposed.mean() > 0.18:
            continue
        coverage = proposed
        selected.append(item)
        if len(selected) == maximum:
            break
    return selected


class FlorenceDetector:
    def __init__(self, directory, device):
        import torch
        from transformers import AutoProcessor, Florence2ForConditionalGeneration

        self.processor = AutoProcessor.from_pretrained(
            directory, local_files_only=True, trust_remote_code=False, use_fast=False
        )
        self.model = (
            Florence2ForConditionalGeneration.from_pretrained(
                directory,
                local_files_only=True,
                torch_dtype=torch.float16 if device == "cuda" else torch.float32,
            )
            .to(device)
            .eval()
        )

    def __call__(self, image, settings):
        import torch

        started = time.perf_counter()
        regions = []
        completed = 0
        windows = [(0, 0, 1, 1), *crop_windows(np.asarray(image), settings.detail_crops)]
        for x0, y0, x1, y1 in windows:
            if completed and time.perf_counter() - started >= settings.detail_seconds:
                break
            crop = image.crop(
                (
                    round(x0 * image.width),
                    round(y0 * image.height),
                    round(x1 * image.width),
                    round(y1 * image.height),
                )
            )
            task = "<DENSE_REGION_CAPTION>"
            inputs = self.processor(text=task, images=crop, return_tensors="pt").to(
                self.model.device
            )
            inputs["pixel_values"] = inputs["pixel_values"].to(self.model.dtype)
            remaining = max(0.1, settings.detail_seconds - (time.perf_counter() - started))
            with torch.inference_mode():
                ids = self.model.generate(
                    **inputs, max_new_tokens=256, num_beams=1, do_sample=False, max_time=remaining
                )
            text = self.processor.batch_decode(ids, skip_special_tokens=False)[0]
            parsed = self.processor.post_process_generation(text, task=task, image_size=crop.size)[
                task
            ]
            for box, label in zip(parsed.get("bboxes", []), parsed.get("labels", [])):
                coords = np.asarray(box, dtype=float)
                if coords.shape != (4,) or not np.isfinite(coords).all():
                    continue
                a, b, c, d = np.clip(
                    coords / [crop.width, crop.height, crop.width, crop.height], 0, 1
                )
                if a < c and b < d:
                    regions.append(
                        {
                            "box": [
                                x0 + a * (x1 - x0),
                                y0 + b * (y1 - y0),
                                x0 + c * (x1 - x0),
                                y0 + d * (y1 - y0),
                            ],
                            "label": str(label)[:160],
                        }
                    )
            completed += 1
        return regions[:64], {
            "completed_views": completed,
            "planned_views": len(windows),
            "budget_exhausted": completed < len(windows),
            "budget_scope": "cooperative decoding; not hard native-call cancellation",
        }
