"""Manufacture candidates before publication; never return an uncertified sheet."""

import hashlib

import numpy as np
from shapely import distance, from_wkt
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from ..geometry import polygon_parts, validate_geometry
from ..models import DesignError
from .bridges import propose_bridge
from .distance import CoreDistanceIndex


def fabricate_sheet(
    original,
    frame,
    profile,
    *,
    max_change_ratio=0.05,
    protected=None,
    support_zones=None,
    locked=None,
    candidate_check=None,
    progress=None,
    occluded=None,
    raster_first=False,
):
    progress = progress or (lambda stage, fraction: None)
    protected = protected if protected is not None else Polygon()
    locked = locked if locked is not None else Polygon()
    occluded = occluded.intersection(frame.artwork_window) if occluded is not None else Polygon()
    exposed_window = frame.artwork_window.difference(occluded)
    dimensions = (frame.spec.outer_width_mm, frame.spec.outer_height_mm)
    if (
        frame.required_frame.difference(original).area > 1e-9
        or frame.required_frame.intersection(protected).area > 1e-9
    ):
        raise DesignError(
            "Required closed frame is missing or conflicts with a protected cutout",
            "INFEASIBLE_CONSTRAINTS",
        )
    # A raster proposal is not an acceptance: validate the proposed continuous
    # result first, instead of certifying a detailed input we are about to replace.
    initial = (
        {"status": "FAIL"}
        if raster_first
        else validate_geometry(original, profile, dimensions, frame=frame)
    )
    if raster_first and initial["status"] != "PASS":
        from .raster_plan import support_candidate

        fast = support_candidate(original, frame, profile, protected, occluded, progress)
        if fast is not None:
            candidate, routes = fast
            delta = candidate.symmetric_difference(original)
            exposed = delta.intersection(exposed_window).area / frame.artwork_window.area
            if (
                exposed <= max_change_ratio + 1e-9
                and candidate.intersection(protected).area < 1e-9
                and locked.difference(candidate).area <= 1e-6
                and all(
                    candidate.intersection(part).area >= 0.8 * part.area
                    for part in polygon_parts(original.intersection(frame.artwork_window))
                    if part.area >= 4 * profile.min_feature_width_mm**2
                )
                and (candidate_check is None or candidate_check(candidate))
            ):
                report = validate_geometry(candidate, profile, dimensions, frame=frame)
                if report["status"] == "PASS":
                    ratio = delta.area / frame.artwork_window.area
                    return candidate, {
                        "status": "PASS",
                        "operation": "production_plan",
                        "planner": "shared-raster-shortest-path-v1",
                        "bridge_count": len(routes),
                        "bridges": routes,
                        "before_wkt": original.wkt,
                        "material_change_ratio": ratio,
                        "exposed_change_ratio": exposed,
                        "hidden_change_ratio": max(0, ratio - exposed),
                        "max_change_ratio": max_change_ratio,
                        "budget_basis": "exposed_artwork"
                        if not occluded.is_empty
                        else "whole_artwork",
                        "source_geometry_sha256": hashlib.sha256(original.wkb).hexdigest(),
                        "output_geometry_sha256": hashlib.sha256(candidate.wkb).hexdigest(),
                        "physical_test_status": "NOT_RUN",
                    }
    if raster_first:
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
    for attempt_index, (rounding, grow) in enumerate(candidates):
        progress(f"attempt-{attempt_index + 1}/{len(candidates)}", attempt_index / len(candidates))
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
            if len(islands) > 256:
                attempts.append(
                    f"Support budget exceeded: {len(islands)} islands require more than 256 routes"
                )
                continue
            # Distance to a growing union is the minimum distance to its members.
            # Update only against the newly joined island/line, not the entire increasingly
            # complex rooted geometry for every island at every iteration.
            from shapely.geometry import GeometryCollection

            rooted_parts = [rooted]
            route_width = max(
                route_profile.min_bridge_width_mm, route_profile.min_feature_width_mm * 1.5
            )
            root_anchor = rooted.buffer(-route_width / 2)
            anchor_parts = [root_anchor] if not root_anchor.is_empty else []
            bridge_lines = []
            island_array = np.asarray(islands, dtype=object)
            remaining = np.ones(len(islands), dtype=bool)
            distances = CoreDistanceIndex(rooted).distances(island_array)
            for route_index in range(len(islands)):
                progress(
                    f"support-{route_index + 1}/{len(islands)}",
                    (attempt_index + 0.2 + 0.6 * route_index / max(1, len(islands)))
                    / len(candidates),
                )
                selected = int(np.argmin(np.where(remaining, distances, np.inf)))
                island = islands[selected]
                island_anchor = island.buffer(-route_width / 2)
                contact_index = STRtree(rooted_parts)

                def root_contact(footprint, contact_index=contact_index):
                    return (
                        sum(
                            footprint.intersection(contact_index.geometries[i]).area
                            for i in contact_index.query(footprint)
                        )
                        > 1e-8
                    )

                endpoint_source = island_anchor if not island_anchor.is_empty else island
                endpoint_target = GeometryCollection(anchor_parts) if anchor_parts else rooted
                if anchor_parts:
                    boundary_index = CoreDistanceIndex(endpoint_target).tree
                    if len(boundary_index.geometries):
                        endpoint_target = boundary_index.geometries[
                            boundary_index.nearest(endpoint_source)
                        ]
                proposal = propose_bridge(
                    island,
                    rooted,
                    frame,
                    route_profile,
                    protected,
                    support_zones=support_zones,
                    root_contact_test=root_contact,
                    endpoint_cores=(
                        endpoint_source,
                        endpoint_target,
                    ),
                )
                if proposal.status != "accepted":
                    rejected = True
                    break
                line = from_wkt(proposal.centerline_wkt)
                rooted_parts.append(island)
                bridge_lines.append(line)
                if not island_anchor.is_empty:
                    anchor_parts.append(island_anchor)
                # Buffer/merge the complete support tree once, after all routes are known.
                rooted = GeometryCollection([*rooted_parts, *bridge_lines])
                bridges.append(
                    {
                        **proposal.model_dump(),
                        "width_mm": 2 * radius,
                        "footprint_wkt": line.buffer(radius, cap_style="round").wkt,
                    }
                )
                remaining[selected] = False
                indices = np.flatnonzero(remaining)
                if len(indices):
                    distances[indices] = np.minimum(
                        distances[indices],
                        np.minimum(
                            CoreDistanceIndex(island).distances(island_array[indices]),
                            distance(island_array[indices], line),
                        ),
                    )
            if remaining.any():
                rejected = True
            if rejected:
                attempts.append("No protected-zone-safe support route")
                continue
            candidate = (
                frame.required_frame.union(rooted.buffer(radius, quad_segs=24))
                .intersection(frame.sheet)
                .difference(protected)
            )
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
        change = candidate.symmetric_difference(original)
        ratio = change.area / frame.artwork_window.area
        exposed_ratio = change.intersection(exposed_window).area / frame.artwork_window.area
        if exposed_ratio > max_change_ratio + 1e-9:
            attempts.append(f"Exposed change {exposed_ratio:.2%} exceeds {max_change_ratio:.0%}")
            continue
        if locked.difference(candidate).area > 1e-6:
            attempts.append("Would remove a locked retained feature")
            continue
        if any(candidate.intersection(part).area < 0.8 * part.area for part in major_parts):
            attempts.append("Would remove more than 20% of a significant source component")
            continue
        if candidate_check is not None and not candidate_check(candidate):
            attempts.append("IMPORTANT_FEATURE_CHANGED: local retained/cutout budget exceeded")
            continue
        progress("check", (attempt_index + 0.9) / len(candidates))
        report = validate_geometry(candidate, profile, dimensions, frame=frame)
        if report["status"] == "PASS" and candidate.intersection(protected).area < 1e-9:
            return candidate, {
                "status": "PASS",
                "operation": "production_plan",
                "bridge_count": len(bridges),
                "bridges": bridges,
                "material_change_ratio": float(ratio),
                "exposed_change_ratio": float(exposed_ratio),
                "hidden_change_ratio": float(max(0, ratio - exposed_ratio)),
                "budget_basis": "exposed_artwork" if not occluded.is_empty else "whole_artwork",
                "max_change_ratio": max_change_ratio,
                "source_geometry_sha256": hashlib.sha256(original.wkb).hexdigest(),
                "output_geometry_sha256": hashlib.sha256(candidate.wkb).hexdigest(),
                "rounding_radius_mm": radius,
                "cut_path_simplification_tolerance_mm": simplify_tolerance,
                "subject_retention_policy": "minimum 80% of each significant component",
                "art_review_status": "pending_review",
                "physical_test_status": "NOT_RUN",
            }
        reasons = sorted({f["code"] for f in report["findings"]})
        blocked = candidate.intersection(protected).area
        if blocked >= 1e-9:
            reasons.append(f"Protected cutout overlap {blocked:.6g} mm2")
        attempts.append(", ".join(reasons) or "Candidate did not satisfy production constraints")
    raise DesignError(
        "Cannot produce a frame-connected sheet within the current profile and change budget: "
        + "; ".join(attempts),
        "INFEASIBLE_CONSTRAINTS",
    )
