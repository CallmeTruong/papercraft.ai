"""Bounded rare-color anchors for coherent, physically sized priority features.

This preserves unusual iris/text/ornament colors without adding sheets or inventing
object-specific colors. The custom paper palette is always authoritative.
"""

import cv2
import numpy as np
from scipy import ndimage as ndi


def reserve_detail_colors(lab, priority, palette, minimum_width_pixels):
    if not priority.any() or len(palette) < 3:
        return palette
    centers = cv2.cvtColor(palette[None].astype(np.float32) / 255, cv2.COLOR_RGB2LAB)[0]
    # Fewer than 8k deterministic global samples for the global-distortion guard.
    sample = lab.reshape(-1, 3)[:: max(1, lab.size // 3 // 4000)]
    base_error = float(np.min(((sample[:, None] - centers[None]) ** 2).sum(2), axis=1).mean())
    minimum_area = max(3, int(np.ceil(minimum_width_pixels**2 * 0.45)))
    # Coarse Lab cells only propose anchors. Actual values are medians of source
    # colors; connected-area eligibility prevents isolated highlights taking slots.
    proposal_lab = cv2.bilateralFilter(lab.astype(np.float32), 5, 12, 2)
    bins = np.floor(proposal_lab / [16, 20, 20]).astype(np.int32)
    _, groups = np.unique(bins[priority], axis=0, return_inverse=True)
    grid = np.full(priority.shape, -1, np.int32)
    grid[priority] = groups
    counts = np.bincount(groups)
    candidates = []
    for group in np.argsort(-counts, kind="stable")[:64]:
        if counts[group] < minimum_area:
            continue
        labels, count = ndi.label(grid == group)
        sizes = np.bincount(labels.ravel())
        for part in range(1, count + 1):
            if sizes[part] < minimum_area:
                continue
            color = np.median(lab[labels == part], axis=0)
            residual = float(np.min(np.linalg.norm(centers - color, axis=1)))
            if residual >= 15:
                candidates.append((residual, color))
    candidates.sort(key=lambda c: -c[0])
    ownership = np.argmin(((lab[priority, None] - centers[None]) ** 2).sum(2), axis=1)
    support = np.bincount(ownership, minlength=len(centers))
    # Existing high-support local colors (e.g. skin planes and a dark outline)
    # are not disposable merely because background colors dominate global error.
    frozen = {
        int(i)
        for i in np.argsort(-support, kind="stable")[: len(centers) - 2]
        if support[i] >= max(5, priority.sum() * 0.04)
    }
    protected = set()
    for _, color in candidates:
        if len(protected) >= min(2, len(palette) // 3):
            break
        if np.min(np.linalg.norm(centers - color, axis=1)) < 15:
            continue
        best = None
        for slot in range(len(centers)):
            if slot in protected or slot in frozen:
                continue
            trial = centers.copy()
            trial[slot] = color
            cost = float(np.min(((sample[:, None] - trial[None]) ** 2).sum(2), axis=1).mean())
            if cost <= base_error + max(32, base_error) and (best is None or cost < best[0]):
                best = cost, slot, trial
        if best is not None:
            _, slot, centers = best
            protected.add(slot)
    converted = cv2.cvtColor(centers[None].astype(np.float32), cv2.COLOR_LAB2RGB)[0]
    return np.unique(np.clip(np.rint(converted * 255), 0, 255).astype(np.uint8), axis=0)


def recover_compact_details(labels, priority, minimum_width_pixels):
    """Bounded dilation of compact priority shapes, preserving neighboring solid cores.

    This proposes a physical target, not a certified cut. No fixed object coordinates,
    new colors or labels. At most eight shapes and 0.75% of the artwork may be changed.
    """
    if not priority.any():
        return labels
    radius = max(1, int(np.ceil(minimum_width_pixels * 0.5)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1,) * 2)
    local_radius = max(1, int(np.floor(minimum_width_pixels * 0.5)))
    local_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (local_radius * 2 + 1,) * 2)
    cores = np.zeros(labels.shape, bool)
    proposals = []
    for color in np.unique(labels):
        mask = labels == color
        color_core = cv2.erode(mask.astype(np.uint8), kernel).astype(bool)
        cores |= color_core
        rarity_boost = 10 if not color_core.any() else 1
        components, _ = ndi.label(mask)
        sizes = np.bincount(components.ravel())
        for index, slices in enumerate(ndi.find_objects(components), 1):
            if slices is None:
                continue
            sy, sx = slices
            if not 3 <= sizes[index] <= max(12, minimum_width_pixels**2 * 10):
                continue
            if max(sy.stop - sy.start, sx.stop - sx.start) > minimum_width_pixels * 4:
                continue
            part = components[sy, sx] == index
            if not np.any(part & priority[sy, sx]):
                continue
            pad = radius + 1
            padded = np.pad(part.astype(np.uint8), pad)
            local_core = cv2.morphologyEx(padded, cv2.MORPH_OPEN, local_kernel)[
                pad:-pad, pad:-pad
            ].astype(bool)
            cores[sy, sx] |= local_core
            if not cv2.erode(padded, kernel).any():
                proposals.append((int(sizes[index]) * rarity_boost, color, sy, sx, part.copy()))
    result = labels.copy()
    changed = 0
    claimed = np.zeros(labels.shape, bool)
    for _, color, sy, sx, part in sorted(proposals, key=lambda p: -p[0])[:8]:
        full = np.zeros(labels.shape, np.uint8)
        full[sy, sx] = part
        growth = (
            cv2.dilate(full, kernel).astype(bool)
            & priority
            & ~(cores & (labels != color))
            & ~claimed
        )
        added = growth & (result != color)
        if changed + int(added.sum()) > labels.size * 0.0075:
            continue
        result[added] = color
        claimed |= growth
        changed += int(added.sum())
    return result
