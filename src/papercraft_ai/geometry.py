"""Millimetre geometry, fabrication diagnostics and bounded reversible repairs."""

import numpy as np
from shapely import contains_xy, make_valid, maximum_inscribed_circle
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .models import DesignError, FabricationProfile

CHECKER_VERSION = "frame-rooted-continuous-3"


def polygon_parts(geometry):
    if isinstance(geometry, Polygon):
        return [] if geometry.is_empty else [geometry]
    if hasattr(geometry, "geoms"):
        return [part for child in geometry.geoms for part in polygon_parts(child)]
    return []


def mask_to_geometry(mask, width_mm, height_mm):
    if mask.ndim != 2 or width_mm <= 0 or height_mm <= 0:
        raise DesignError("Mask and physical dimensions are invalid")
    h, w = mask.shape
    sx, sy = width_mm / w, height_mm / h
    rectangles = []
    # Runs model pixel cells exactly, avoiding contour-center half-pixel scale errors.
    for y, row in enumerate(mask):
        changes = np.diff(np.pad(row.astype(np.int8), (1, 1)))
        starts, ends = np.where(changes == 1)[0], np.where(changes == -1)[0]
        rectangles.extend(
            box(x0 * sx, y * sy, x1 * sx, (y + 1) * sy) for x0, x1 in zip(starts, ends)
        )
    exact = unary_union(rectangles) if rectangles else Polygon()
    if exact.is_empty:
        return exact
    candidate = exact.simplify(min(sx, sy) * 0.7, preserve_topology=True)
    raster = geometry_to_mask(candidate, mask.shape, width_mm, height_mm)
    mismatch = np.count_nonzero(raster ^ mask) / max(1, np.count_nonzero(raster | mask))
    same_topology = len(polygon_parts(candidate)) == len(polygon_parts(exact)) and sum(
        len(p.interiors) for p in polygon_parts(candidate)
    ) == sum(len(p.interiors) for p in polygon_parts(exact))
    return (
        candidate
        if mismatch <= 0.01 and same_topology
        else exact.simplify(0, preserve_topology=True)
    )


def geometry_to_mask(geometry, shape, width_mm, height_mm):
    h, w = shape
    xs = (np.arange(w) + 0.5) * width_mm / w
    ys = (np.arange(h) + 0.5) * height_mm / h
    return contains_xy(geometry, xs[None, :], ys[:, None])


def supported(part, sheet, width):
    # A point contact cannot mechanically support a paper sheet.
    return part.intersection(sheet.boundary).length >= width - 1e-8


def finding(code, geometry, message, measured=None, required=None, severity="error"):
    point = geometry.representative_point()
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "location_mm": [float(point.x), float(point.y)],
        "measured": measured,
        "required": required,
        "repaired": False,
    }


def validate_geometry(
    geometry, profile: FabricationProfile, dimensions, attachment=None, *, frame=None
):
    from .fabrication.models import FrameSpec
    from .fabrication.support import certify_support

    if frame is None:
        frame = FrameSpec(
            outer_width_mm=dimensions[0],
            outer_height_mm=dimensions[1],
            border_width_mm=profile.min_frame_width_mm,
        )
    report = certify_support(geometry, frame, profile)
    if geometry.is_valid and not geometry.is_empty:
        for part in polygon_parts(geometry):
            for ring in part.interiors:
                hole = Polygon(ring)
                diameter = (
                    2
                    * maximum_inscribed_circle(hole, tolerance=profile.min_hole_size_mm / 32).length
                )
                if diameter + 1e-6 < profile.min_hole_size_mm:
                    report["findings"].append(
                        finding(
                            "TINY_HOLE",
                            hole,
                            "Cutout is below the profile minimum",
                            diameter,
                            profile.min_hole_size_mm,
                        )
                    )
    report["status"] = "FAIL" if report["findings"] else "PASS"
    return report


def repair_geometry(
    geometry,
    profile,
    dimensions,
    max_change_ratio=0.05,
    attachment=None,
    *,
    frame=None,
    protected=None,
    support_zones=None,
):
    width, height = dimensions
    sheet = box(0, 0, width, height)
    from .fabrication.bridges import propose_bridge
    from .fabrication.frame import build_frame
    from .fabrication.models import FrameSpec

    frame = frame or build_frame(
        FrameSpec(
            outer_width_mm=width, outer_height_mm=height, border_width_mm=profile.min_frame_width_mm
        )
    )
    protected = protected if protected is not None else Polygon()
    original = geometry
    current = geometry
    records = []

    def accept(candidate, operation):
        nonlocal current
        candidate = unary_union(polygon_parts(make_valid(candidate))).intersection(sheet)
        ratio = candidate.symmetric_difference(original).area / frame.artwork_window.area
        if (
            candidate.is_empty
            or not candidate.is_valid
            or ratio > max_change_ratio + 1e-12
            or candidate.intersection(protected).area > 1e-9
            or frame.required_frame.difference(candidate).area > 1e-9
        ):
            return False
        if candidate.equals(current):
            return False
        records.append(
            {
                "operation": operation,
                "before_wkt": current.wkt,
                "after_wkt": candidate.wkt,
                "changed_area_mm2": float(candidate.symmetric_difference(current).area),
                "cumulative_change_ratio": float(ratio),
            }
        )
        current = candidate
        return True

    if not current.is_valid:
        accept(make_valid(current), "topology_repair")
    if not current.is_valid:
        return current, records
    # Missing frames are not silently synthesized by a support repair.
    if frame.required_frame.difference(current).area > 1e-9:
        return current, records
    for _ in range(len(polygon_parts(current))):
        parts = polygon_parts(current)
        roots = [part for part in parts if part.intersection(frame.required_frame).area > 1e-9]
        islands = [part for part in parts if part.intersection(frame.required_frame).area <= 1e-9]
        if not islands or not roots:
            break
        target = unary_union(roots)
        island = min(islands, key=lambda part: part.distance(target))
        proposal = propose_bridge(
            island, target, frame, profile, protected, support_zones=support_zones
        )
        if proposal.status != "accepted":
            break
        from shapely import from_wkt

        bridge = from_wkt(proposal.footprint_wkt)
        if not accept(current.union(bridge), "bridge"):
            break
        records[-1].update(
            bridge_width_mm=proposal.width_mm,
            centerline_wkt=proposal.centerline_wkt,
            current_footprint_wkt=bridge.difference(from_wkt(records[-1]["before_wkt"])).wkt,
            strategy=proposal.strategy,
            enabled=True,
        )
    filled = []
    for part in polygon_parts(current):
        holes = [
            ring
            for ring in part.interiors
            if 2
            * maximum_inscribed_circle(
                Polygon(ring), tolerance=profile.min_hole_size_mm / 16
            ).length
            >= profile.min_hole_size_mm
        ]
        filled.append(Polygon(part.exterior, holes))
    accept(unary_union(filled), "remove_tiny_holes")
    report = validate_geometry(current, profile, dimensions, attachment, frame=frame)
    if any(f["code"] == "THIN_FEATURE" for f in report["findings"]):
        accept(
            current.buffer(profile.min_feature_width_mm * 0.55).intersection(sheet),
            "thicken_material",
        )
    if profile.cut_tolerance_mm and not records:
        accept(
            current.simplify(profile.cut_tolerance_mm, preserve_topology=True).union(
                frame.required_frame
            ),
            "simplify_cut_paths",
        )
    # Return the actual status after every accepted operation, not just the initial report.
    for record in records:
        from shapely import from_wkt

        after = from_wkt(record["after_wkt"])
        record["validation_after"] = validate_geometry(
            after, profile, dimensions, attachment, frame=frame
        )
    return current, records
