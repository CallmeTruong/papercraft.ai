"""Authored forest/deer reference: real sheet geometry, not an image mockup."""

import numpy as np
from shapely.affinity import scale
from shapely.geometry import LineString, Point, box, mapping

from ..models import DesignConfig
from ..pipeline import generate_design, png_bytes
from .models import SceneObject, ScenePlan


def stroke(points, width):
    return LineString(points).buffer(width / 2, quad_segs=12)


def ellipse(x, y, rx, ry):
    return scale(Point(x, y).buffer(1, quad_segs=24), rx, ry)


def forest_reference(mode="multicolor"):
    colors = (
        ["#193c3a", "#37635b", "#648b79", "#9fb49a", "#d3d9b9"]
        if mode == "multicolor"
        else ["#f4eedf"] * 5
    )
    objects = []

    def add(name, g, index, role):
        objects.append(
            SceneObject(
                object_id=name,
                geometry=mapping(g.intersection(box(10, 10, 190, 150))),
                layer_index=index,
                color=colors[index],
                support_role=role,
            )
        )

    for i, y in enumerate([134, 128, 138, 142, 146]):
        add(f"ground-{i}", box(10, y, 190, 150), i, "ground")
    # Large foreground trees make natural support paths to the frame, top and ground.
    for x, direction in [(24, 1), (177, -1)]:
        tree = stroke([(x, 150), (x + direction * 2, 85), (x - direction * 2, 8)], 6)
        for y, length in [(30, 30), (48, 24), (65, 18)]:
            tree = tree.union(stroke([(x, y + 16), (x + direction * length, y)], 3))
            tree = tree.union(ellipse(x + direction * length, y, 12, 7))
        add(f"foreground-tree-{x}", tree, 0, "tree")
    # The legs overlap both the body and ground; antler branches use >=1.8mm strokes.
    deer = ellipse(96, 111, 14, 7).union(ellipse(112, 96, 6, 4))
    deer = deer.union(stroke([(105, 111), (109, 98)], 6))
    for points in [[(87, 113), (85, 130)], [(94, 114), (96, 130)], [(104, 113), (107, 130)]]:
        deer = deer.union(stroke(points, 2.8))
    for points in [
        [(110, 94), (106, 84), (108, 77)],
        [(107, 86), (101, 83)],
        [(107, 81), (103, 78)],
        [(114, 94), (119, 85), (118, 77)],
        [(118, 86), (124, 81)],
        [(119, 82), (123, 77)],
    ]:
        deer = deer.union(stroke(points, 1.8))
    deer = deer.union(stroke([(84, 110), (79, 105)], 2.5))
    add("deer", deer, 1, "subject")
    for index, x in [(2, 50), (2, 153), (3, 70), (3, 166), (4, 42), (4, 144)]:
        trunk = stroke([(x, 150), (x, 80)], 3 if index == 2 else 2.4)
        for y in [95, 106, 118, 130]:
            spread = (y - 74) * 0.16
            trunk = trunk.union(stroke([(x - spread, y + 5), (x, y), (x + spread, y + 5)], 2))
        add(f"distant-tree-{index}-{x}", trunk, index, "tree")
    # Low rounded hills continue into the lower frame, never an opaque full-sheet stopper.
    for index, x, y in [(2, 30, 151), (3, 100, 163), (4, 150, 157)]:
        add(f"hill-{index}", ellipse(x, y, 65, 23), index, "ground")
    moon = Point(118, 39).buffer(10, quad_segs=24)
    plan = ScenePlan(
        objects=objects,
        protected_light_apertures=[mapping(moon)],
        provenance="authored:forest-deer-v1",
    )
    source = np.full((140, 180, 3), 240, np.uint8)
    config = DesignConfig(
        mode=mode, colors=5, layers=5, width_mm=200, height_mm=160, frame_mm=10, detail=1
    )
    return generate_design(png_bytes(source), config, scene_plan=plan)
