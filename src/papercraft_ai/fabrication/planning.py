"""Bounded opaque color-order proposals; every proposal preserves the flat target."""

import hashlib

import numpy as np
from scipy import ndimage as ndi


def color_orders(result):
    colors, areas = np.unique(result.color_labels, return_counts=True)
    masks = {int(c): result.color_labels == c for c in colors}

    costs = {}

    def cost(mask):
        key = hashlib.blake2b(mask, digest_size=16).digest()
        if key in costs:
            return costs[key]
        components, count = ndi.label(mask)
        border = np.unique(
            np.concatenate([components[0], components[-1], components[:, 0], components[:, -1]])
        )
        rooted = np.isin(components, border[border > 0])
        islands = count - np.count_nonzero(border)
        ids = np.setdiff1d(np.arange(1, count + 1), border)
        if ids.size and rooted.any():
            distances = ndi.distance_transform_edt(~rooted)
            areas = np.bincount(components.ravel())
            effort = float(np.sum(ndi.minimum(distances, components, ids) * np.sqrt(areas[ids])))
            effort /= max(1, np.sqrt(rooted.sum()))
        else:
            effort = float(mask.size) if ids.size else 0.0
        # A handful of long exposed bridges can be worse than many short hidden
        # connections. Rank their estimated length/area, not island count alone.
        value = effort, max(0, islands), int((mask & ~rooted).sum()), -int(mask.sum())
        costs[key] = value
        return value

    def greedy(first=None):
        order = [] if first is None else [first]
        occupied = np.zeros(result.regions.shape, bool) if first is None else masks[first].copy()
        remaining = set(masks).difference(order)
        while remaining:
            selected = min(remaining, key=lambda c: (*cost(occupied | masks[c]), c))
            order.append(selected)
            occupied |= masks[selected]
            remaining.remove(selected)
        return order

    ranked = sorted(masks, key=lambda c: (*cost(masks[c]), c))
    proposals = [greedy(), [int(c) for c in colors[np.argsort(-areas, kind="stable")]]]
    if len(ranked) > 1:
        proposals.append(greedy(ranked[1]))
    return list(dict.fromkeys(tuple(order) for order in proposals))
