"""Per-feature geometric constraints, checked after fabrication and on later edits."""

from scipy import ndimage
from shapely.affinity import translate
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from ..geometry import finding, mask_to_geometry, polygon_parts


def feature_constraints(design):
    perception = design.abstraction.perception
    if perception is None or not perception.features.any():
        return []
    labels, count = ndimage.label(perception.features)
    constraints = []
    for feature in range(1, count + 1):
        border = design.frame.spec.border_width_mm
        roi = translate(
            mask_to_geometry(
                labels == feature, design.width_mm - 2 * border, design.height_mm - 2 * border
            ),
            border,
            border,
        )
        if roi.is_empty:
            continue
        for layer, geometry in zip(design.layers, design.geometries):
            constraints.append(
                {
                    "layer_id": layer.id,
                    "feature_id": feature,
                    "roi": mapping(roi),
                    "retained": mapping(unary_union(polygon_parts(geometry.intersection(roi)))),
                    "max_change": design.config.ai.max_feature_change if design.config.ai else 0.10,
                }
            )
    return constraints


def feature_findings(design, layer_id, geometry):
    from ..workflows import settings

    issues, measurements = [], []
    constraints = [c for c in design.feature_constraints if c["layer_id"] == layer_id]
    if not constraints:
        return issues, measurements
    workflow = settings(design.config)
    hidden = None
    if workflow.family in ("multicolor", "depth_scene") and workflow.lighting == "ambient":
        index = next(i for i, layer in enumerate(design.layers) if layer.id == layer_id)
        hidden = unary_union(design.geometries[:index])
    for constraint in constraints:
        roi = shape(constraint["roi"])
        target = shape(constraint["retained"])
        if hidden is not None:
            roi = roi.difference(hidden)
            target = target.intersection(roi)
        if roi.is_empty:
            continue
        actual = geometry.intersection(roi)
        # Separate positive/negative errors: a tiny eye opening cannot disappear in a large ROI.
        removed = target.difference(actual).area / max(target.area, 1e-9)
        waste = roi.difference(target)
        filled = actual.intersection(waste).area / max(waste.area, 1e-9)
        measurements.append(
            {
                "feature_id": constraint["feature_id"],
                "removed_ratio": removed,
                "filled_ratio": filled,
            }
        )
        if max(removed, filled) > constraint["max_change"] + 1e-6:
            issues.append(
                finding(
                    "IMPORTANT_FEATURE_CHANGED",
                    roi,
                    "An important feature exceeds its local material/cutout change budget",
                )
            )
    return issues, measurements


def feature_support_zones(design, layer_id):
    rois = [shape(c["roi"]) for c in design.feature_constraints if c["layer_id"] == layer_id]
    if not rois:
        return None
    # Prefer supports outside facial features. Local positive/negative budgets remain
    # hard acceptance gates, while authored scene cutouts remain exact exclusions.
    return design.frame.artwork_window.difference(unary_union(rois))
