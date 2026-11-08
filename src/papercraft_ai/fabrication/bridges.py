"""Route physical supports; every candidate is checked again in continuous geometry."""

import heapq
import math
from uuid import uuid4

import numpy as np
from scipy.ndimage import distance_transform_edt
from shapely import contains_xy
from shapely.geometry import LineString, Polygon
from shapely.ops import nearest_points

from .models import BridgeProposal


def propose_bridge(island, rooted, frame, profile, protected=None, support_zones=None):
    forbidden = protected if protected is not None else Polygon()
    width = max(profile.min_bridge_width_mm, profile.min_feature_width_mm * 1.5)
    island_core, rooted_core = island.buffer(-width / 2), rooted.buffer(-width / 2)
    a, b = nearest_points(
        island_core if not island_core.is_empty else island,
        rooted_core if not rooted_core.is_empty else rooted,
    )
    line = LineString([a, b])

    def accepted(candidate):
        footprint = candidate.buffer(width / 2, cap_style="square", join_style="round")
        return (
            frame.sheet.covers(footprint)
            and footprint.intersection(forbidden).area < 1e-9
            and footprint.intersection(island).area > 1e-8
            and footprint.intersection(rooted).area > 1e-8
        ), footprint

    ok, footprint = accepted(line)
    direct = (line, footprint, ok)
    strategy = "direct"
    preferred = support_zones is not None and not support_zones.is_empty
    if not ok or preferred:
        strategy = "astar"
        step = max(0.25, min(width / 3, 1.0), math.sqrt(frame.sheet.area / 1_000_000))
        nx = math.ceil(frame.spec.outer_width_mm / step)
        ny = math.ceil(frame.spec.outer_height_mm / step)
        xs, ys = (np.arange(nx) + 0.5) * step, (np.arange(ny) + 0.5) * step
        clearance = width / 2 + math.sqrt(2) * step
        blocked = contains_xy(forbidden.buffer(clearance), xs[None, :], ys[:, None])
        blocked |= ~contains_xy(frame.sheet.buffer(-width / 2), xs[None, :], ys[:, None])
        starts = contains_xy(island, xs[None, :], ys[:, None]) & ~blocked
        goals = contains_xy(rooted, xs[None, :], ys[:, None]) & ~blocked
        natural = (
            contains_xy(support_zones.union(island).union(rooted), xs[None, :], ys[:, None])
            if preferred
            else np.ones((ny, nx), bool)
        )
        route = None
        if starts.any() and goals.any():
            heuristic = distance_transform_edt(~goals)
            start = min(zip(*np.where(starts)), key=lambda p: heuristic[p])
            queue = [(float(heuristic[start]), 0.0, start)]
            costs, parent = {start: 0.0}, {}
            while queue:
                _, cost, node = heapq.heappop(queue)
                if cost > costs[node]:
                    continue
                if goals[node]:
                    route = [node]
                    while route[-1] in parent:
                        route.append(parent[route[-1]])
                    break
                for dy, dx in (
                    (0, 1),
                    (0, -1),
                    (1, 0),
                    (-1, 0),
                    (1, 1),
                    (1, -1),
                    (-1, 1),
                    (-1, -1),
                ):
                    nxt = (node[0] + dy, node[1] + dx)
                    if not (0 <= nxt[0] < ny and 0 <= nxt[1] < nx) or blocked[nxt]:
                        continue
                    new_cost = cost + math.hypot(dx, dy) * (1 if natural[nxt] else 4)
                    if new_cost < costs.get(nxt, math.inf):
                        costs[nxt], parent[nxt] = new_cost, node
                        heapq.heappush(queue, (new_cost + heuristic[nxt], new_cost, nxt))
        if route and len(route) > 1:
            line = LineString([(xs[x], ys[y]) for y, x in reversed(route)])
            ok, footprint = accepted(line)
        else:
            ok = False

        def visual_cost(candidate):
            return (
                candidate.length
                if not preferred
                else candidate.intersection(support_zones).length
                + 4 * candidate.difference(support_zones).length
            )

        if direct[2] and (not ok or visual_cost(direct[0]) <= visual_cost(line)):
            line, footprint, ok = direct
            strategy = "direct"
    return BridgeProposal(
        proposal_id=uuid4().hex,
        centerline_wkt=line.wkt,
        footprint_wkt=footprint.wkt,
        width_mm=width,
        cost=float(
            (
                line.length
                if not preferred
                else line.intersection(support_zones).length
                + 4 * line.difference(support_zones).length
            )
            * width
        ),
        strategy=strategy,
        status="accepted" if ok else "rejected",
        reasons=[] if ok else ["No support route clears protected cutouts at the required width"],
    )
