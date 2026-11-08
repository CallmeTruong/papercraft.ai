"""Conservative continuous support proofs rooted in a complete closed paper frame."""

import math

from shapely import points
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .frame import build_frame

CHECKER_VERSION = "frame-rooted-continuous-3"


def parts(geometry):
    if isinstance(geometry, Polygon):
        return [] if geometry.is_empty else [geometry]
    return [p for child in getattr(geometry, "geoms", []) for p in parts(child)]


def diagnostic(code, geometry, message, measured=None, required=None):
    point = geometry.representative_point()
    return {
        "code": code,
        "severity": "error",
        "message": message,
        "location_mm": [float(point.x), float(point.y)],
        "measured": measured,
        "required": required,
        "repaired": False,
    }


class CornerIndex:
    """Evaluate vertex eligibility once; query only corners close to each residual patch."""

    def __init__(self, material, width, tolerance):
        vertices, eligible = [], []
        for polygon in parts(material):
            polygon = orient(polygon, sign=1)
            for ring in [polygon.exterior, *polygon.interiors]:
                coords = list(ring.coords)[:-1]
                for i, vertex in enumerate(coords):
                    previous, following = coords[i - 1], coords[(i + 1) % len(coords)]
                    a = (vertex[0] - previous[0], vertex[1] - previous[1])
                    b = (following[0] - vertex[0], following[1] - vertex[1])
                    cross = a[0] * b[1] - a[1] * b[0]
                    turn = math.atan2(cross, a[0] * b[0] + a[1] * b[1])
                    vertices.append(vertex)
                    eligible.append(
                        cross > 0
                        and min(math.hypot(*a), math.hypot(*b)) >= width - tolerance
                        and math.pi - turn >= math.pi / 2 - 1e-6
                    )
        self.tree = STRtree(points(vertices))
        self.eligible = eligible

    def accepts(self, patch, tolerance):
        nearby = self.tree.query(patch, predicate="dwithin", distance=tolerance)
        return len(nearby) == 1 and self.eligible[nearby[0]]


def bounded_convex_tip(patch, material, width, tolerance, *, corner_index=None):
    """Certify only an isolated >=90° corner with two sufficiently long incident edges.

    Thin branch ends have multiple close corners or a short incident edge and cannot use this policy.
    """
    minx, miny, maxx, maxy = patch.bounds
    if math.hypot(maxx - minx, maxy - miny) > math.sqrt(2) * width / 2 + tolerance:
        return False
    index = corner_index if corner_index is not None else CornerIndex(material, width, tolerance)
    return index.accepts(patch, tolerance)


def certify_support(material, frame, profile):
    findings = []
    frame_geo = build_frame(frame) if not hasattr(frame, "required_frame") else frame
    spec = frame_geo.spec
    tol = profile.validation_tolerance_mm
    if material.is_empty or not material.is_valid or not parts(material):
        findings.append(
            diagnostic(
                "INVALID_GEOMETRY",
                frame_geo.sheet,
                "Material must be nonempty valid polygon geometry",
            )
        )
        return {
            "status": "FAIL",
            "findings": findings,
            "coverage": {"checker_version": CHECKER_VERSION},
            "metrics": {},
        }
    outside = material.difference(frame_geo.sheet)
    if outside.area > 1e-8:
        findings.append(
            diagnostic("OUTSIDE_SHEET", outside, "Material extends outside the physical sheet")
        )
    missing = frame_geo.required_frame.difference(material)
    if missing.area > 1e-9:
        findings.append(
            diagnostic(
                "FRAME_MISSING",
                missing,
                "Every sheet must retain the complete closed paper frame",
                missing.area,
                0,
            )
        )
    if spec.border_width_mm < profile.min_frame_width_mm:
        findings.append(
            diagnostic(
                "THIN_FRAME",
                frame_geo.required_frame,
                "Frame is below profile width",
                spec.border_width_mm,
                profile.min_frame_width_mm,
            )
        )
    components = parts(material)
    if len(components) != 1:
        findings.append(
            diagnostic(
                "DISCONNECTED_MATERIAL",
                material,
                "Retained material must be one continuous frame-rooted sheet",
                len(components),
                1,
            )
        )
    for component in components:
        if component.intersection(frame_geo.required_frame).area <= 1e-9:
            findings.append(
                diagnostic(
                    "UNSUPPORTED_ISLAND",
                    component,
                    "Material island is disconnected from the required frame",
                )
            )
    width = profile.min_feature_width_mm
    neck_radius = profile.min_bridge_width_mm / 2
    neck_frame = frame_geo.required_frame.buffer(-neck_radius, quad_segs=24)
    neck_islands = [
        part
        for part in parts(material.buffer(-neck_radius, quad_segs=24))
        if part.intersection(neck_frame).area <= 1e-9
    ]
    if neck_islands:
        findings.append(
            diagnostic(
                "THIN_SUPPORT_NECK",
                unary_union(neck_islands),
                "A retained body loses its frame-rooted core at the minimum support width",
                required=profile.min_bridge_width_mm,
            )
        )
    radius = width / 2
    core = material.buffer(-radius, quad_segs=24)
    core_frame = frame_geo.required_frame.buffer(-radius, quad_segs=24)
    core_parts = parts(core)
    root_parts = [part for part in core_parts if part.intersection(core_frame).area > 1e-9]
    unrooted = [part for part in core_parts if part.intersection(core_frame).area <= 1e-9]
    if unrooted:
        findings.append(
            diagnostic(
                "THIN_NECK",
                unary_union(unrooted),
                "Minimum-width core has no positive-width path to the frame",
                required=width,
            )
        )
    rooted = unary_union(root_parts)
    covered = frame_geo.required_frame.union(rooted.buffer(radius + tol, quad_segs=24))
    unresolved = material.difference(covered)
    accepted_tips = []
    corner_index = (
        CornerIndex(material, width, tol)
        if profile.decorative_tip_policy == "bounded_convex" and not unresolved.is_empty
        else None
    )
    for patch in parts(unresolved):
        if patch.area <= 1e-10:
            continue
        if profile.decorative_tip_policy == "bounded_convex" and bounded_convex_tip(
            patch, material, width, tol, corner_index=corner_index
        ):
            accepted_tips.append(patch.bounds)
        else:
            findings.append(
                diagnostic(
                    "THIN_FEATURE",
                    patch,
                    "Material is not covered by a frame-rooted minimum-width core",
                    required=width,
                )
            )
    status = "FAIL" if findings else "PASS"
    return {
        "status": status,
        "findings": findings,
        "coverage": {
            "checker_version": CHECKER_VERSION,
            "units": "mm",
            "connectivity_method": "full-frame containment and positive-area polygon components",
            "width_method": "continuous rooted inset core + geometric coverage",
            "error_bound_mm": tol,
            "required_feature_width_mm": width,
            "required_support_width_mm": profile.min_bridge_width_mm,
            "decorative_tip_policy": profile.decorative_tip_policy,
            "certified_convex_corners": accepted_tips,
            "physical_test_status": "NOT_RUN",
        },
        "metrics": {
            "material_component_count": len(components),
            "missing_frame_area_mm2": float(missing.area),
            "unsupported_island_count": sum(f["code"] == "UNSUPPORTED_ISLAND" for f in findings),
            "thin_feature_violation_count": sum(
                f["code"] in ("THIN_FEATURE", "THIN_NECK", "THIN_SUPPORT_NECK") for f in findings
            ),
            "total_cut_length_mm": float(material.boundary.length),
            "cutting_path_count": sum(1 + len(p.interiors) for p in components),
            "invalid_polygon_count": 0,
        },
        "frame": spec.model_dump(),
    }
