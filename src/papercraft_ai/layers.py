"""Physical sheet construction: a color region is not a sheet of paper."""

import heapq
from dataclasses import dataclass, field

import numpy as np

from .imaging import AbstractionResult
from .models import DesignConfig, DesignError, hex_color
from .workflows import settings


@dataclass
class PaperLayer:
    id: str
    color: str
    mask: np.ndarray
    index: int
    spacing_mm: float
    region_ids: list[int] = field(default_factory=list)
    attachment: str | None = None
    depth_source: str | None = None


def order_constraints(ids, edges):
    if len(set(ids)) != len(ids):
        raise DesignError("Duplicate layer ids")
    children = {key: set() for key in ids}
    incoming = {key: 0 for key in ids}
    rank = {key: i for i, key in enumerate(ids)}
    for before, after in edges:
        if before not in children or after not in children:
            raise DesignError("Unknown layer in order constraints")
        if after not in children[before]:
            children[before].add(after)
            incoming[after] += 1
    ready = [(rank[key], key) for key in ids if incoming[key] == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        _, key = heapq.heappop(ready)
        ordered.append(key)
        for child in children[key]:
            incoming[child] -= 1
            if incoming[child] == 0:
                heapq.heappush(ready, (rank[child], child))
    if len(ordered) != len(ids):
        raise DesignError("Occlusion constraints contain a cycle", "INFEASIBLE_CONSTRAINTS")
    return ordered


def construct_layers(
    result: AbstractionResult, config: DesignConfig, depths=None, region_groups=None
) -> list[PaperLayer]:
    groups = []
    if settings(config).family != "depth_scene":
        if region_groups is not None:
            assigned = [i for group in region_groups for i in group]
            if sorted(assigned) != list(range(len(result.region_info))):
                raise DesignError("Region groups must partition every region exactly once")
            for group in region_groups:
                colors = {result.region_info[i]["color_index"] for i in group}
                if len(colors) != 1:
                    raise DesignError("One paper sheet must use one paper color")
                groups.append(
                    (
                        np.isin(result.regions, group),
                        group,
                        hex_color(result.palette[next(iter(colors))]),
                        None,
                    )
                )
        else:
            colors, areas = np.unique(result.color_labels, return_counts=True)
            # Background/large regions in front provide a frame around the openings.
            # Hidden material behind front layers supports later color sheets.
            for color in colors[np.argsort(-areas, kind="stable")]:
                ids = [r["id"] for r in result.region_info if r["color_index"] == color]
                groups.append(
                    (result.color_labels == color, ids, hex_color(result.palette[color]), None)
                )
    else:
        count = config.layers
        if depths is None:
            # Explicitly labeled heuristic, not a claimed physical depth estimate.
            depths = {}
            for region in result.region_info:
                ys, _ = np.where(result.regions == region["id"])
                depths[region["id"]] = min(
                    count - 1, int((1 - ys.mean() / len(result.regions)) * count)
                )
            depth_source = "heuristic_vertical_position"
        else:
            depths = {int(k): v for k, v in depths.items()}
            if set(depths) != set(range(len(result.region_info))):
                raise DesignError("Manual depth must assign every region exactly once")
            if any(type(v) is not int or not 0 <= v < count for v in depths.values()):
                raise DesignError("Manual depth must be an integer within the layer budget")
            depth_source = "manual"
        for depth in sorted(set(depths.values())):
            ids = sorted(k for k, value in depths.items() if value == depth)
            groups.append((np.isin(result.regions, ids), ids, "#f4eedf", depth_source))
    if len(groups) > config.layers:
        raise DesignError(
            f"Need {len(groups)} sheets but layer limit is {config.layers}",
            "INFEASIBLE_CONSTRAINTS",
        )
    layers = []
    accumulated = np.zeros(result.regions.shape, bool)
    for index, (visible_mask, ids, color, depth_source) in enumerate(groups):
        accumulated |= visible_mask
        mask = (
            visible_mask.copy()
            if config.assembly_mode == "mounted_pieces" or settings(config).family == "depth_scene"
            else accumulated.copy()
        )
        if settings(config).family == "depth_scene" and settings(config).lighting == "backlit":
            # Bright source is a provisional aperture proposal, explicitly not object-aware depth.
            mask &= np.mean(result.original, axis=2) < 225
        mask.setflags(write=False)
        layers.append(
            PaperLayer(
                f"layer-{index + 1:02}",
                color,
                mask,
                index,
                0 if settings(config).assembly == "flat" and config.workflow else config.spacing_mm,
                ids,
                "Glue individual pieces to the backing in the supplied order"
                if config.assembly_mode == "mounted_pieces"
                else None,
                depth_source,
            )
        )
    return layers


def composite_layers(layers) -> np.ndarray:
    if not layers:
        raise DesignError("A design must contain at least one layer")
    from .models import rgb

    result = np.zeros((*layers[0].mask.shape, 4), np.uint8)
    for layer in reversed(layers):
        if layer.mask.shape != result.shape[:2]:
            raise DesignError("Layer masks must share one working grid")
        result[layer.mask] = (*rgb(layer.color), 255)
    return result


def layer_metrics(layers, target):
    composite = composite_layers(layers)
    mismatch = np.any(composite[:, :, :3] != target, axis=2) | (composite[:, :, 3] != 255)
    return {
        "reconstruction_error": float(mismatch.mean()),
        "uncovered_pixel_ratio": float((composite[:, :, 3] == 0).mean()),
        "layer_count": len(layers),
        "layer_utilization": [float(layer.mask.mean()) for layer in layers],
    }
