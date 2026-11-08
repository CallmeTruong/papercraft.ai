"""One shared shortest-path field for frame-rooted support proposals.

Raster proposals never certify paper: continuous geometry and the existing checker
remain authoritative. This avoids re-buffering a growing polygon for every island.
"""

import numpy as np
from scipy import ndimage as ndi
from shapely import maximum_inscribed_circle
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
from skimage.graph import MCP_Geometric

from ..geometry import geometry_to_mask, polygon_parts


def support_candidate(original, frame, profile, protected, occluded, progress):
    width = max(profile.min_bridge_width_mm, profile.min_feature_width_mm * 1.5)
    radius = profile.min_feature_width_mm / 2 + 0.04
    support_radius = width / 2 + 0.04
    w, h = frame.spec.outer_width_mm, frame.spec.outer_height_mm
    pitch = max(width / 4, max(w, h) / 768)
    shape = (max(2, int(np.ceil(h / pitch))), max(2, int(np.ceil(w / pitch))))
    rounded = original.buffer(width * 0.35, quad_segs=8).buffer(-width * 0.35, quad_segs=8)
    core = rounded.buffer(-radius, quad_segs=8)
    root = frame.required_frame.buffer(-radius, quad_segs=4)
    pieces = polygon_parts(core)
    islands = [p for p in pieces if p.intersection(root).area < 1e-9]
    if len(islands) > 256:
        return None
    if not islands:
        return frame.required_frame.union(core.buffer(radius, quad_segs=8)).difference(
            protected
        ), []
    retained = geometry_to_mask(original, shape, w, h)
    hidden = geometry_to_mask(occluded, shape, w, h)
    costs = np.where(retained | hidden, 1.0, 40.0)
    # Keep every proposed footprint inside the sheet and away from hard cutouts.
    allowed = frame.sheet.buffer(-support_radius).difference(
        protected.buffer(support_radius + pitch)
    )
    costs[~geometry_to_mask(allowed, shape, w, h)] = np.inf
    roots = geometry_to_mask(root, shape, w, h) & np.isfinite(costs)
    # A boundary seed is sufficient and much smaller than all pixels in the frame.
    roots &= ~ndi.binary_erosion(roots)
    starts = list(zip(*np.where(roots)))
    if not starts:
        return None
    progress("raster-distance-field", 0.1)
    solver = MCP_Geometric(costs, fully_connected=True)
    distances, _ = solver.find_costs(starts=starts)
    lines = []
    for i, island in enumerate(islands):
        progress(f"raster-support-{i + 1}/{len(islands)}", 0.15 + 0.6 * i / len(islands))
        possible = geometry_to_mask(island, shape, w, h) & np.isfinite(distances)
        anchor = None
        if possible.any():
            end = np.unravel_index(np.argmin(np.where(possible, distances, np.inf)), shape)
        else:
            # Continuous cores smaller than a pixel still need physical support.
            anchor = island.representative_point()
            end = (
                min(shape[0] - 1, max(0, int(anchor.y / h * shape[0]))),
                min(shape[1] - 1, max(0, int(anchor.x / w * shape[1]))),
            )
            if not np.isfinite(distances[end]):
                return None
        path = solver.traceback(end)
        if len(path) > 1:
            coordinates = [((x + 0.5) * w / shape[1], (y + 0.5) * h / shape[0]) for y, x in path]
            if anchor is not None:
                coordinates.append((anchor.x, anchor.y))
            lines.append(LineString(coordinates))
    supports = unary_union(lines).buffer(support_radius + pitch * 0.12, quad_segs=8)
    material = frame.required_frame.union(core.buffer(radius, quad_segs=8)).union(supports)
    material = material.intersection(frame.sheet).difference(protected)
    filled = []
    for part in polygon_parts(material):
        holes = []
        for ring in part.interiors:
            hole = Polygon(ring)
            if (
                hole.intersection(protected).area > 1e-9
                or 2
                * maximum_inscribed_circle(hole, tolerance=profile.min_hole_size_mm / 32).length
                + 1e-6
                >= profile.min_hole_size_mm
            ):
                holes.append(ring)
        filled.append(Polygon(part.exterior, holes))
    material = unary_union(filled)
    tolerance = min(profile.cut_tolerance_mm * 0.25, profile.validation_tolerance_mm * 0.5)
    if tolerance:
        material = material.simplify(tolerance, preserve_topology=True).difference(protected)
    receipts = [
        {
            "centerline_wkt": line.wkt,
            "footprint_wkt": line.buffer(support_radius + pitch * 0.12, quad_segs=8).wkt,
            "width_mm": 2 * (support_radius + pitch * 0.12),
            "strategy": "shared-raster-geodesic",
            "status": "accepted",
        }
        for line in lines
    ]
    return material, receipts
