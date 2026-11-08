"""Physical-scale categorical regularization before any polygon construction."""

import cv2
import numpy as np
from scipy import ndimage as ndi


def manufacturable_labels(labels, config, perception=None):
    if config.manufacturing_width_mm is None:
        return labels
    height = config.height_mm or config.width_mm * labels.shape[0] / labels.shape[1]
    pitch = min(
        (config.width_mm - 2 * config.frame_mm) / labels.shape[1],
        (height - 2 * config.frame_mm) / labels.shape[0],
    )
    if pitch <= 0:
        return labels
    radius = min(12, max(1, int(np.ceil(config.manufacturing_width_mm / pitch * 0.35))))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1,) * 2)
    # Open each category, then fill only the removed narrow material from surviving
    # categories. Unlike a median on integer labels, the operation is color-order neutral.
    cores = np.zeros(labels.shape, bool)
    for color in np.unique(labels):
        cores |= cv2.morphologyEx(
            (labels == color).astype(np.uint8), cv2.MORPH_OPEN, kernel
        ).astype(bool)
    if perception is not None:
        cores |= perception.features
    if not cores.any():
        return labels
    nearest = ndi.distance_transform_edt(~cores, return_distances=False, return_indices=True)
    result = labels[tuple(nearest)].copy()
    if perception is not None:
        result[perception.features] = labels[perception.features]
    return result
