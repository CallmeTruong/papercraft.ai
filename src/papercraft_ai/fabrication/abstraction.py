"""Physical-scale categorical regularization before any polygon construction."""

import cv2
import numpy as np
from scipy import ndimage as ndi


def detail_mask(shape, config, perception=None):
    mask = np.zeros(shape, bool)
    if perception is not None:
        mask |= perception.features
    h, w = shape
    for x0, y0, x1, y1 in config.detail_boxes:
        mask[
            int(y0 * h) : max(int(y0 * h) + 1, int(np.ceil(y1 * h))),
            int(x0 * w) : max(int(x0 * w) + 1, int(np.ceil(x1 * w))),
        ] = True
    return mask


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
    if config.detail_boxes:
        # A protected eye/mouth is not a request to freeze every photographic
        # scale or single-pixel glint. Remove isolated categorical speckles while
        # retaining substantial local shapes before freezing the detail target.
        selected = detail_mask(labels.shape, config)
        if perception is not None:
            selected &= ~perception.features
        labels = labels.copy()
        colors = np.unique(labels)
        for _ in range(2):
            votes = np.stack(
                [
                    cv2.boxFilter((labels == c).astype(np.float32), -1, (3, 3), normalize=False)
                    for c in colors
                ]
            )
            winner = colors[votes.argmax(axis=0)]
            own = np.zeros(labels.shape, np.float32)
            for i, c in enumerate(colors):
                own[labels == c] = votes[i][labels == c]
            replace = selected & (own < 3) & (votes.max(axis=0) >= 5)
            labels[replace] = winner[replace]
    radius = min(12, max(1, int(np.ceil(config.manufacturing_width_mm / pitch * 0.35))))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1,) * 2)
    # Open each category, then fill only the removed narrow material from surviving
    # categories. Unlike a median on integer labels, the operation is color-order neutral.
    cores = np.zeros(labels.shape, bool)
    for color in np.unique(labels):
        cores |= cv2.morphologyEx(
            (labels == color).astype(np.uint8), cv2.MORPH_OPEN, kernel
        ).astype(bool)
    protected = detail_mask(labels.shape, config, perception)
    cores |= protected
    if not cores.any():
        return labels
    nearest = ndi.distance_transform_edt(~cores, return_distances=False, return_indices=True)
    result = labels[tuple(nearest)].copy()
    result[protected] = labels[protected]
    return result
