"""Cheap binary candidates; connected-sheet proof is always supplied by fabrication."""

import numpy as np
from scipy import ndimage
from skimage.color import rgb2gray
from skimage.filters import threshold_otsu, threshold_sauvola

from .layers import PaperLayer
from .workflows import settings


def stencil_layer(abstraction, config):
    gray = rgb2gray(abstraction.original)
    wf = settings(config)
    threshold = float(threshold_otsu(gray)) if gray.max() > gray.min() else float(gray.mean())
    thresholds = [threshold, *np.quantile(gray, [0.3, 0.4, 0.5, 0.6]).tolist()]
    candidates = []
    for value in thresholds:
        mask = gray <= value
        if wf.style_options.polarity == "light_paper":
            mask = ~mask
        candidates.append(mask)
    if wf.style == "detailed" and min(gray.shape) >= 15:
        local = threshold_sauvola(gray, window_size=15)
        candidates.append(
            gray < local if wf.style_options.polarity == "dark_paper" else gray >= local
        )

    def cost(mask):
        _, components = ndimage.label(mask)
        area = float(mask.mean())
        deviation = float(np.count_nonzero(mask ^ candidates[0]) / mask.size)
        return deviation + 0.001 * components + max(0, 0.05 - area) + max(0, area - 0.95)

    mask = min(candidates, key=cost).copy()
    color = "#182e2b" if wf.style_options.polarity == "dark_paper" else "#f4eedf"
    abstraction.metrics.update(
        stencil_candidate_count=len(candidates), art_review_status="pending_review"
    )
    return [
        PaperLayer(
            "layer-01",
            color,
            mask,
            0,
            0 if wf.assembly == "flat" else config.spacing_mm,
            list(range(len(abstraction.region_info))),
        )
    ]
