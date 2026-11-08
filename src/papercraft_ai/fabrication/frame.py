"""A required paper frame is geometry, not contact with the image boundary."""

from shapely.affinity import translate
from shapely.geometry import box

from .models import FrameGeometry, FrameSpec, RasterToMmTransform


def build_frame(spec: FrameSpec) -> FrameGeometry:
    w, h, b = spec.outer_width_mm, spec.outer_height_mm, spec.border_width_mm
    sheet = box(0, 0, w, h)
    window = box(b, b, w - b, h - b)
    return FrameGeometry(spec, sheet, window, sheet.difference(window))


def artwork_transform(spec: FrameSpec, raster_shape: tuple[int, int]) -> RasterToMmTransform:
    h, w = raster_shape
    if h <= 0 or w <= 0:
        raise ValueError("Raster dimensions must be positive")
    b = spec.border_width_mm
    return RasterToMmTransform(
        (spec.outer_width_mm - 2 * b) / w, (spec.outer_height_mm - 2 * b) / h, b, b
    )


def material_from_mask(mask, frame: FrameGeometry, *, abstraction_tolerance_mm=0):
    from ..geometry import mask_to_geometry

    b = frame.spec.border_width_mm
    content = mask_to_geometry(
        mask, frame.spec.outer_width_mm - 2 * b, frame.spec.outer_height_mm - 2 * b
    )
    if abstraction_tolerance_mm:
        # Artistic contour fitting is distinct from machine cut tolerance. The
        # independent final color/structure gate still measures against the mask.
        content = content.simplify(abstraction_tolerance_mm, preserve_topology=True)
    return frame.required_frame.union(translate(content, b, b))
