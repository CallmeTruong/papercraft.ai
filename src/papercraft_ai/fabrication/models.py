"""Physical scene contracts. All geometry is in millimetres, with a top-left origin."""

import math
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from shapely.errors import GEOSException
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry


class PhysicalModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, validate_default=True
    )


class FrameSpec(PhysicalModel):
    outer_width_mm: float = Field(gt=0, le=2000)
    outer_height_mm: float = Field(gt=0, le=2000)
    border_width_mm: float = Field(gt=0)

    @model_validator(mode="after")
    def has_window(self):
        if 2 * self.border_width_mm >= min(self.outer_width_mm, self.outer_height_mm):
            raise ValueError("Frame must leave a positive artwork window")
        return self


@dataclass(frozen=True)
class FrameGeometry:
    spec: FrameSpec
    sheet: BaseGeometry
    artwork_window: BaseGeometry
    required_frame: BaseGeometry


@dataclass(frozen=True)
class RasterToMmTransform:
    scale_x: float
    scale_y: float
    offset_x: float
    offset_y: float

    def pixel_center(self, x: int, y: int):
        return (self.offset_x + (x + 0.5) * self.scale_x, self.offset_y + (y + 0.5) * self.scale_y)


class SceneObject(PhysicalModel):
    object_id: str = Field(min_length=1, max_length=100)
    geometry: dict
    layer_index: int = Field(ge=0, le=23, strict=True)
    role: Literal["retain", "cutout"] = "retain"
    support_role: Literal["subject", "ground", "tree", "frame", "background", "diffuser"] = (
        "subject"
    )
    color: str = "#f4eedf"
    locked: bool = True
    region_ids: list[int] = Field(default_factory=list)
    opacity_class: Literal["opaque", "translucent"] = "opaque"

    @field_validator("geometry")
    @classmethod
    def polygon(cls, value):
        return finite_polygon(value)

    @field_validator("color")
    @classmethod
    def valid_color(cls, value):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Paper color must be #RRGGBB")
        return value.lower()


def finite_polygon(value):
    try:
        geometry = shape(value)
        if (
            geometry.geom_type not in ("Polygon", "MultiPolygon")
            or geometry.is_empty
            or not geometry.is_valid
            or not all(math.isfinite(v) for v in geometry.bounds)
        ):
            raise ValueError("Scene geometry must be a finite valid nonempty polygon")
    except (KeyError, TypeError, GEOSException) as exc:
        raise ValueError("Invalid scene polygon") from exc
    return value


class ScenePlan(PhysicalModel):
    objects: list[SceneObject] = Field(default_factory=list, max_length=2000)
    occlusion_edges: list[tuple[str, str]] = Field(default_factory=list)
    protected_cutouts: list[dict] = Field(default_factory=list)
    allowed_support_zones: list[dict] = Field(default_factory=list)
    protected_light_apertures: list[dict] = Field(default_factory=list)
    provenance: str = "manual"

    @field_validator("protected_cutouts", "protected_light_apertures", "allowed_support_zones")
    @classmethod
    def polygons(cls, values):
        return [finite_polygon(value) for value in values]

    @model_validator(mode="after")
    def unique_objects(self):
        ids = [o.object_id for o in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("Scene object ids must be unique")
        if any(a not in ids or b not in ids for a, b in self.occlusion_edges):
            raise ValueError("Occlusion references an unknown object")
        return self


class PrintLayout(PhysicalModel):
    paper: Literal["A4", "A3"] = "A4"
    orientation: Literal["auto", "portrait", "landscape"] = "auto"
    margin_mm: float = Field(default=10, ge=5, le=40)
    overlap_mm: float = Field(default=10, ge=0, le=40)
    print_side: Literal["front", "back"] = "front"
    scale: Literal[1.0] = 1.0
    ruler_mm: float = Field(default=20, gt=0, le=50)


class BridgeProposal(PhysicalModel):
    proposal_id: str
    centerline_wkt: str
    footprint_wkt: str
    width_mm: float = Field(gt=0)
    cost: float = Field(ge=0)
    strategy: Literal["direct", "astar"]
    status: Literal["proposed", "accepted", "rejected", "infeasible"] = "proposed"
    reasons: list[str] = Field(default_factory=list)
