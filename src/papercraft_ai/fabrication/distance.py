"""Exact nearest-boundary distances between disjoint material cores."""

import numpy as np
from shapely.geometry import LineString
from shapely.strtree import STRtree

from ..geometry import polygon_parts


class CoreDistanceIndex:
    def __init__(self, material):
        chunks = []
        for polygon in polygon_parts(material):
            for ring in [polygon.exterior, *polygon.interiors]:
                coords = np.asarray(ring.coords)
                for start in range(0, len(coords) - 1, 16):
                    chunks.append(LineString(coords[start : start + 17]))
        self.tree = STRtree(chunks)

    def distances(self, disjoint_cores):
        result = np.full(len(disjoint_cores), np.inf)
        if len(disjoint_cores) and len(self.tree.geometries):
            indices, distances = self.tree.query_nearest(
                disjoint_cores, all_matches=False, return_distance=True
            )
            result[indices[0]] = distances
        return result
