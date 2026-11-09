"""Physical sheet construction: a color region is not a sheet of paper."""

import heapq
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi

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
    result: AbstractionResult,
    config: DesignConfig,
    depths=None,
    region_groups=None,
    *,
    color_order=None,
) -> list[PaperLayer]:
    groups = []
    color_depth = (
        settings(config).family == "depth_scene"
        and (
            config.depth_paper == "multicolor"
            or (config.depth_paper is None and settings(config).lighting == "ambient")
        )
        and config.depth_paper != "white"
        and config.manufacturing_width_mm is not None
        and depths is None
    )
    if settings(config).family != "depth_scene" or color_depth:
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
            ordered = (
                colors[np.argsort(-areas, kind="stable")] if color_order is None else color_order
            )
            if sorted(ordered) != sorted(colors):
                raise DesignError("Color order must contain every used color exactly once")
            for color in ordered:
                ids = [r["id"] for r in result.region_info if r["color_index"] == color]
                groups.append(
                    (
                        result.color_labels == color,
                        ids,
                        hex_color(result.palette[color]),
                        "color_occlusion" if color_depth else None,
                    )
                )
    else:
        count = config.layers
        model_depths = None
        if depths is None and config.depth_paper == "white":
            # Ordered tone silhouettes preserve internal shapes in neutral paper;
            # this is explicitly tonal relief, not recovered object depth.
            brightness = result.palette.astype(float) @ np.array([0.2126, 0.7152, 0.0722])
            ranks = {int(c): i for i, c in enumerate(np.argsort(brightness, kind="stable"))}
            model_depths = {
                r["id"]: round(ranks[r["color_index"]] * (count - 1) / max(1, len(ranks) - 1))
                for r in result.region_info
            }
        elif depths is None:
            from .ai.perception import propose_depths

            model_depths = propose_depths(result, count)
        if model_depths is not None:
            depths = model_depths
            depth_source = (
                "tonal_relief" if config.depth_paper == "white" else "model_relative_depth"
            )
        elif depths is None:
            # Explicitly labeled heuristic, not a claimed physical depth estimate.
            depths = {}
            for region in result.region_info:
                x0, y0, x1, y1 = region["bbox_px"]
                ys, _ = np.where(result.regions[y0:y1, x0:x1] == region["id"])
                depths[region["id"]] = min(
                    count - 1, int((1 - (ys.mean() + y0) / len(result.regions)) * count)
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
            if config.depth_paper == "multicolor":
                for color in sorted({result.region_info[i]["color_index"] for i in ids}):
                    selected = [i for i in ids if result.region_info[i]["color_index"] == color]
                    groups.append(
                        (
                            np.isin(result.regions, selected),
                            selected,
                            hex_color(result.palette[color]),
                            depth_source,
                        )
                    )
                continue
            groups.append(
                (
                    np.isin(result.regions, ids),
                    ids,
                    "#ffffff" if config.depth_paper == "white" else "#f4eedf",
                    depth_source,
                )
            )
    if (
        config.manufacturing_width_mm is not None
        and settings(config).lighting == "ambient"
        and (settings(config).family != "depth_scene" or color_depth)
    ):
        # Put frame-rooted portions in front, and postpone isolated same-color
        # details behind intervening colors. E.g. green leaves and green irises
        # need not share one exposed support tree across the face.
        early, deferred = [], []
        spare = config.layers - len(groups)
        for mask, ids, color, source in groups:
            components, _ = ndi.label(mask)
            border = np.unique(
                np.concatenate([components[0], components[-1], components[:, 0], components[:, -1]])
            )
            rooted = mask & np.isin(components, border[border > 0])
            remaining = mask & ~rooted
            if spare > 0 and rooted.any() and remaining.any():
                first_ids = [i for i in ids if np.any(rooted & (result.regions == i))]
                later_ids = [i for i in ids if i not in first_ids]
                early.append((rooted, first_ids, color, source))
                deferred.append((remaining, later_ids, color, source))
                spare -= 1
            else:
                early.append((mask, ids, color, source))
        # Keep the final backing color last: a continuous rear sheet needs no
        # visible support through all the foreground's detail openings.
        groups = (
            (early + deferred if len(early) == 2 else early[:-1] + deferred + early[-1:])
            if deferred
            else early
        )
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
            visible_mask.copy() if config.assembly_mode == "mounted_pieces" else accumulated.copy()
        )
        # Rear depth sheets retain the foreground silhouettes too: N/ N-1/ .../1
        # material intersections, rather than one disjoint sheet per image region.
        # Source brightness never punches identical holes through the whole stack.
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
