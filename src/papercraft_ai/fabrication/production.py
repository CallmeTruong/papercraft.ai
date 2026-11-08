"""Manufacture candidates before publication; never return an uncertified sheet."""

import hashlib

from shapely import from_wkt
from shapely.geometry import Polygon
from shapely.ops import unary_union

from ..geometry import polygon_parts, validate_geometry
from ..models import DesignError
from .bridges import propose_bridge


def fabricate_sheet(
    original,
    frame,
    profile,
    *,
    max_change_ratio=0.05,
    protected=None,
    support_zones=None,
    locked=None,
):
    protected = protected if protected is not None else Polygon()
    locked = locked if locked is not None else Polygon()
    dimensions = (frame.spec.outer_width_mm, frame.spec.outer_height_mm)
    if (
        frame.required_frame.difference(original).area > 1e-9
        or frame.required_frame.intersection(protected).area > 1e-9
    ):
        raise DesignError(
            "Required closed frame is missing or conflicts with a protected cutout",
            "INFEASIBLE_CONSTRAINTS",
        )
    initial = validate_geometry(original, profile, dimensions, frame=frame)
    source_art = original.intersection(frame.artwork_window)
    major_parts = [
        p for p in polygon_parts(source_art) if p.area >= 4 * profile.min_feature_width_mm**2
    ]
    width = max(profile.min_feature_width_mm, profile.min_bridge_width_mm)
    attempts = []
    candidates = (
        [(0, 0)] if initial["status"] == "PASS" else [(1, 0), (1, 0.2), (1.2, 0), (1.4, 0.2)]
    )
    for rounding, grow in candidates:
        radius = width * 0.5 * rounding + (0.04 if rounding else 0)
        if rounding:
            # Closing removes sub-tool-sized cutouts; opening replaces pixel tips with cuttable arcs.
            candidate = original.buffer(width * 0.35, quad_segs=16).buffer(
                -width * 0.35, quad_segs=16
            )
            if grow:
                candidate = candidate.buffer(grow, quad_segs=16)
            candidate = candidate.buffer(-radius, quad_segs=24).buffer(radius, quad_segs=24)
            candidate = frame.required_frame.union(
                candidate.intersection(frame.artwork_window)
            ).difference(protected)
            if grow:
                fragile = [
                    part
                    for part in major_parts
                    if candidate.intersection(part).area < 0.8 * part.area
                ]
                candidate = (
                    candidate.union(
                        unary_union([part.buffer(radius, quad_segs=24) for part in fragile])
                    )
                    .intersection(frame.sheet)
                    .difference(protected)
                )
        else:
            candidate = original
        bridges = []
        rejected = False
        if rounding:
            core = candidate.buffer(-radius, quad_segs=24)
            root_frame = frame.required_frame.buffer(-radius, quad_segs=24)
            roots = [
                part for part in polygon_parts(core) if part.intersection(root_frame).area > 1e-9
            ]
            islands = [
                part for part in polygon_parts(core) if part.intersection(root_frame).area <= 1e-9
            ]
            rooted = unary_union([root_frame, *roots])
            route_profile = profile.model_copy(
                update={"min_bridge_width_mm": max(profile.min_bridge_width_mm, 2 * radius + 0.15)}
            )
            for _ in range(min(256, len(islands))):
                island = min(islands, key=lambda part: part.distance(rooted))
                proposal = propose_bridge(
                    island, rooted, frame, route_profile, protected, support_zones=support_zones
                )
                if proposal.status != "accepted":
                    rejected = True
                    break
                line = from_wkt(proposal.centerline_wkt)
                rooted = unary_union([rooted, island, line])
                bridges.append(
                    {
                        **proposal.model_dump(),
                        "width_mm": 2 * radius,
                        "footprint_wkt": line.buffer(radius, cap_style="round").wkt,
                    }
                )
                islands.remove(island)
            if islands:
                rejected = True
            if rejected:
                attempts.append("No protected-zone-safe support route")
                continue
            candidate = frame.required_frame.union(
                rooted.buffer(radius, quad_segs=24)
            ).intersection(frame.sheet)
            # Only fill holes below the declared minimum; protected cutouts remain hard constraints.
            from shapely import maximum_inscribed_circle
            from shapely.geometry import Polygon as PaperPolygon

            filled = []
            for part in polygon_parts(candidate):
                holes = []
                for ring in part.interiors:
                    hole = PaperPolygon(ring)
                    diameter = (
                        2
                        * maximum_inscribed_circle(
                            hole, tolerance=profile.min_hole_size_mm / 32
                        ).length
                    )
                    if (
                        diameter + 1e-6 >= profile.min_hole_size_mm
                        or hole.intersection(protected).area > 1e-9
                    ):
                        holes.append(ring)
                filled.append(PaperPolygon(part.exterior, holes))
            candidate = unary_union(filled)
        simplify_tolerance = min(profile.cut_tolerance_mm * 0.1, profile.validation_tolerance_mm)
        if rounding and simplify_tolerance > 0 and locked.is_empty:
            simplified = frame.required_frame.union(
                candidate.simplify(simplify_tolerance, preserve_topology=True)
            )
            if (
                simplified.intersection(protected).area < 1e-9
                and validate_geometry(simplified, profile, dimensions, frame=frame)["status"]
                == "PASS"
            ):
                candidate = simplified
        ratio = candidate.symmetric_difference(original).area / frame.artwork_window.area
        if ratio > max_change_ratio + 1e-9:
            attempts.append(f"Material change {ratio:.2%} exceeds {max_change_ratio:.0%}")
            continue
        if locked.difference(candidate).area > 1e-6:
            attempts.append("Would remove a locked retained feature")
            continue
        if any(candidate.intersection(part).area < 0.8 * part.area for part in major_parts):
            attempts.append("Would remove more than 20% of a significant source component")
            continue
        report = validate_geometry(candidate, profile, dimensions, frame=frame)
        if report["status"] == "PASS" and candidate.intersection(protected).area < 1e-9:
            return candidate, {
                "status": "PASS",
                "operation": "production_plan",
                "bridge_count": len(bridges),
                "bridges": bridges,
                "material_change_ratio": float(ratio),
                "max_change_ratio": max_change_ratio,
                "source_geometry_sha256": hashlib.sha256(original.wkb).hexdigest(),
                "output_geometry_sha256": hashlib.sha256(candidate.wkb).hexdigest(),
                "rounding_radius_mm": radius,
                "cut_path_simplification_tolerance_mm": simplify_tolerance,
                "subject_retention_policy": "minimum 80% of each significant component",
                "art_review_status": "pending_review",
                "physical_test_status": "NOT_RUN",
            }
        attempts.append(", ".join(sorted({f["code"] for f in report["findings"]})))
    raise DesignError(
        "Cannot produce a frame-connected sheet within the current profile and change budget: "
        + "; ".join(attempts),
        "INFEASIBLE_CONSTRAINTS",
    )
