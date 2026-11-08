"""Independent appearance checks on the final physical polygons, not a mockup."""

import cv2
import numpy as np
from skimage.metrics import structural_similarity

from ..models import rgb
from ..workflows import settings


def image_fidelity(design):
    if (
        design.config.manufacturing_width_mm is None
        or settings(design.config).lighting != "ambient"
    ):
        return {
            "status": "NOT_EVALUATED",
            "reason": "Requires versioned ambient color construction",
        }
    composite = design.composite()[..., :3]
    h, w = composite.shape[:2]
    b = design.config.frame_mm
    x0, x1 = round(b / design.width_mm * w), round((design.width_mm - b) / design.width_mm * w)
    y0, y1 = round(b / design.height_mm * h), round((design.height_mm - b) / design.height_mm * h)
    actual = composite[y0:y1, x0:x1]
    expected = design.abstraction.flat.copy()
    recolored = False
    for layer in design.layers:
        assigned = np.isin(design.abstraction.regions, layer.region_ids)
        color = rgb(layer.color)
        recolored |= bool(np.any(expected[assigned] != color))
        expected[assigned] = color
    target = cv2.resize(
        expected, (actual.shape[1], actual.shape[0]), interpolation=cv2.INTER_NEAREST
    )
    color_match = float(np.mean(np.all(actual == target, axis=2)))
    # Low-frequency source similarity detects gross subject loss without requiring
    # individual fur fibers or photographic texture to be cut from paper.
    source = cv2.resize(design.abstraction.original, (96, 96), interpolation=cv2.INTER_AREA)
    final = cv2.resize(actual, (96, 96), interpolation=cv2.INTER_AREA)
    similarity = float(structural_similarity(source, final, channel_axis=2, data_range=255))
    return {
        "status": "PASS"
        if color_match >= 0.94 and (similarity >= 0.40 or recolored or design.config.palette)
        else "FAIL",
        "target_color_agreement": color_match,
        "source_structure_similarity": similarity,
        "minimum_color_agreement": 0.94,
        "minimum_source_similarity": 0.40,
        "source_similarity_gate": not recolored and not design.config.palette,
        "recognition_review": "NOT_RUN",
        "scope": "automated coarse appearance checks; not semantic eye/nose recognition",
        "construction": "color-occlusion relief"
        if settings(design.config).family == "depth_scene"
        else "multicolor",
    }
