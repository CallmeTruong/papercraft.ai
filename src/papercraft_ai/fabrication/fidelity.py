"""Independent appearance checks on the final physical polygons, not a mockup."""

import cv2
import numpy as np
from scipy import ndimage
from skimage.metrics import structural_similarity

from ..models import rgb
from ..workflows import settings


def image_fidelity(design):
    if (
        design.config.manufacturing_width_mm is None
        or (
            settings(design.config).family == "depth_scene" and design.config.depth_paper == "white"
        )
        or settings(design.config).lighting != "ambient"
    ):
        return {
            "status": "NOT_EVALUATED",
            "reason": "Requires versioned ambient color construction",
        }
    composite = design.composite()[..., :3]
    h, w = composite.shape[:2]
    b = design.config.frame_mm
    us = ((np.arange(w) + 0.5) * design.width_mm / w - b) / (design.width_mm - 2 * b)
    vs = ((np.arange(h) + 0.5) * design.height_mm / h - b) / (design.height_mm - 2 * b)
    columns = np.flatnonzero((us >= 0) & (us < 1))
    rows = np.flatnonzero((vs >= 0) & (vs < 1))
    actual = composite[np.ix_(rows, columns)]
    us, vs = us[columns], vs[rows]
    expected = design.abstraction.flat.copy()
    recolored = False
    for layer in design.layers:
        assigned = np.isin(design.abstraction.regions, layer.region_ids)
        color = rgb(layer.color)
        recolored |= bool(np.any(expected[assigned] != color))
        expected[assigned] = color
    target = expected[
        np.ix_(
            np.minimum((vs * expected.shape[0]).astype(int), expected.shape[0] - 1),
            np.minimum((us * expected.shape[1]).astype(int), expected.shape[1] - 1),
        )
    ]
    color_match = float(np.mean(np.all(actual == target, axis=2)))
    # Low-frequency source similarity detects gross subject loss without requiring
    # individual fur fibers or photographic texture to be cut from paper.
    source = cv2.resize(design.abstraction.original, (96, 96), interpolation=cv2.INTER_AREA)
    final = cv2.resize(actual, (96, 96), interpolation=cv2.INTER_AREA)
    similarity = float(structural_similarity(source, final, channel_axis=2, data_range=255))
    regions = []
    for i, (x0, y0, x1, y1) in enumerate(design.config.detail_boxes):
        region = (vs[:, None] >= y0) & (vs[:, None] < y1) & (us[None, :] >= x0) & (us[None, :] < x1)
        agreement = (
            float(np.mean(np.all(actual[region] == target[region], axis=1)))
            if region.any()
            else 0.0
        )
        regions.append(
            {
                "index": i + 1,
                "color_agreement": agreement,
                "minimum_agreement": 0.90,
                "status": "PASS"
                if agreement >= 0.90
                else ("FAIL" if design.config.detail_policy == "strict" else "WARNING"),
            }
        )
    detail_pass = all(r["status"] == "PASS" for r in regions)
    automatic = []
    perception = design.abstraction.perception
    proposals = list((perception.important_regions or []) if perception else [])
    if perception is not None and design.config.abstraction_policy in (
        "cut_aware_v2",
        "detail_aware_v3",
    ):
        labels, _ = ndimage.label(perception.features)
        height, width = labels.shape
        for slices in ndimage.find_objects(labels)[:8]:
            if slices is None:
                continue
            sy, sx = slices
            proposals.append(
                {
                    "box": [sx.start / width, sy.start / height, sx.stop / width, sy.stop / height],
                    "label": "Unverified landmark proposal",
                    "source": "landmark_priority",
                }
            )
    for i, proposal in enumerate(proposals):
        x0, y0, x1, y1 = proposal["box"]
        selected = (
            (vs[:, None] >= y0) & (vs[:, None] < y1) & (us[None, :] >= x0) & (us[None, :] < x1)
        )
        agreement = (
            float(np.mean(np.all(actual[selected] == target[selected], axis=1)))
            if selected.any()
            else 0.0
        )
        source_local = design.abstraction.original[
            int(y0 * design.abstraction.original.shape[0]) : max(
                int(y0 * design.abstraction.original.shape[0]) + 1,
                int(np.ceil(y1 * design.abstraction.original.shape[0])),
            ),
            int(x0 * design.abstraction.original.shape[1]) : max(
                int(x0 * design.abstraction.original.shape[1]) + 1,
                int(np.ceil(x1 * design.abstraction.original.shape[1])),
            ),
        ]
        row, col = np.where(selected)
        contrast_ratio = 0.0
        source_detail_similarity = 0.0
        if row.size:
            local = actual[row.min() : row.max() + 1, col.min() : col.max() + 1]
            source_detail_similarity = float(
                structural_similarity(
                    cv2.resize(source_local, (24, 24), interpolation=cv2.INTER_AREA),
                    cv2.resize(local, (24, 24), interpolation=cv2.INTER_AREA),
                    channel_axis=2,
                    data_range=255,
                )
            )

            # Lab variation checks that a colorful/high-contrast source detail did
            # not collapse to one paper color. It does not certify semantic identity.
            def contrast(pixels):
                lab = cv2.cvtColor(pixels.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
                return float(np.linalg.norm(lab.std(axis=(0, 1))))

            contrast_ratio = contrast(local) / max(contrast(source_local), 1.0)
        passed = agreement >= 0.90 and contrast_ratio >= 0.25
        if design.config.abstraction_policy == "detail_aware_v3":
            passed = passed and source_detail_similarity >= 0.45
        automatic.append(
            {
                "index": i + 1,
                "box": proposal["box"],
                "label": proposal["label"],
                "source": proposal["source"],
                "color_agreement": agreement,
                "contrast_ratio": contrast_ratio,
                "source_detail_similarity": source_detail_similarity,
                "status": "PASS" if passed else "WARNING",
            }
        )
    automatic_pass = all(r["status"] == "PASS" for r in automatic)
    return {
        "status": (
            "PASS"
            if detail_pass and automatic_pass
            else "WARNING"
            if detail_pass
            else ("FAIL" if design.config.detail_policy == "strict" else "WARNING")
        )
        if color_match >= 0.94 and (similarity >= 0.40 or recolored or design.config.palette)
        else "FAIL",
        "target_color_agreement": color_match,
        "source_structure_similarity": similarity,
        "minimum_color_agreement": 0.94,
        "minimum_source_similarity": 0.40,
        "source_similarity_gate": not recolored and not design.config.palette,
        "detail_regions": regions,
        "automatic_detail_regions": automatic,
        "recognition_review": "NOT_RUN",
        "scope": "automated coarse appearance checks; not semantic eye/nose recognition",
        "construction": "color-occlusion relief"
        if settings(design.config).family == "depth_scene"
        else "multicolor",
    }
