"""Compile authored semantic objects to frame-connected material candidates."""

from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

from ..geometry import geometry_to_mask
from ..layers import PaperLayer, order_constraints
from ..models import DesignConfig, DesignError


def protected_geometry(plan, layer_index=None):
    if plan is None:
        return Polygon()
    return unary_union(
        [shape(g) for g in [*plan.protected_cutouts, *plan.protected_light_apertures]]
        + [
            shape(o.geometry)
            for o in plan.objects
            if o.role == "cutout" and o.locked and o.layer_index == layer_index
        ]
    )


def scene_index(plan, layer_id):
    if plan is None:
        return None
    indices = sorted({o.layer_index for o in plan.objects if o.role == "retain"})
    rank = int(layer_id.split("-")[1]) - 1
    return indices[rank] if 0 <= rank < len(indices) else None


def check_scene_order(plan, layer_ids):
    if plan is None:
        return
    indices = sorted({o.layer_index for o in plan.objects if o.role == "retain"})
    by_object = {o.object_id: o for o in plan.objects}
    positions = {
        index: layer_ids.index(f"layer-{rank + 1:02}") for rank, index in enumerate(indices)
    }
    for front, back in plan.occlusion_edges:
        if positions[by_object[front].layer_index] > positions[by_object[back].layer_index]:
            raise DesignError(
                "Layer order violates scene occlusion constraints", "INFEASIBLE_CONSTRAINTS"
            )


def compile_scene(plan, frame, config, raster_shape):
    if not plan.objects:
        raise DesignError("Scene requires retained objects")
    order_constraints([o.object_id for o in plan.objects], plan.occlusion_edges)
    by_id = {o.object_id: o for o in plan.objects}
    for front, back in plan.occlusion_edges:
        if by_id[front].layer_index > by_id[back].layer_index:
            raise DesignError(
                "Scene layer order conflicts with occlusion constraints", "INFEASIBLE_CONSTRAINTS"
            )
    protected = protected_geometry(plan)
    layers, geometries = [], []
    indices = sorted({o.layer_index for o in plan.objects if o.role == "retain"})
    if len(indices) > config.layers:
        raise DesignError("Scene exceeds the cut-sheet budget", "INFEASIBLE_CONSTRAINTS")
    for i, index in enumerate(indices):
        objects = [o for o in plan.objects if o.layer_index == index and o.role == "retain"]
        colors = {DesignConfig.color(o.color) for o in objects}
        if len(colors) != 1:
            raise DesignError("Each physical sheet must have one paper color")
        for obj in objects:
            if obj.opacity_class != "opaque" or obj.support_role == "diffuser":
                raise DesignError(
                    "Custom translucent cut sheets are unavailable; use a separate uncut diffuser",
                    "CAPABILITY_UNAVAILABLE",
                )
            geometry = shape(obj.geometry)
            if not geometry.is_valid or geometry.is_empty or not frame.sheet.covers(geometry):
                raise DesignError("Scene object geometry is invalid or outside the sheet")
        retained = unary_union([shape(o.geometry) for o in objects]).intersection(
            frame.artwork_window
        )
        cutouts = unary_union(
            [
                shape(o.geometry)
                for o in plan.objects
                if o.layer_index == index and o.role == "cutout"
            ]
        )
        material = frame.required_frame.union(retained.difference(cutouts.union(protected)))
        mask = geometry_to_mask(
            material, raster_shape, frame.spec.outer_width_mm, frame.spec.outer_height_mm
        )
        layers.append(
            PaperLayer(
                f"layer-{i + 1:02}",
                next(iter(colors)),
                mask,
                i,
                0.0
                if config.workflow and config.workflow.assembly == "flat"
                else config.spacing_mm,
                [],
            )
        )
        geometries.append(material)
    return layers, geometries
