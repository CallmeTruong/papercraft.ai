"""Validated configuration and errors shared by the CPU pipeline."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .workflows import WorkflowSettings


class DesignError(ValueError):
    def __init__(self, message: str, code: str = "INVALID_INPUT"):
        super().__init__(message)
        self.code = code


class AISettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    segmentation: bool = False
    depth: bool = False
    face_features: bool = False
    important_details: bool = False
    detail_crops: int = Field(default=1, ge=0, le=2, strict=True)
    detail_seconds: float = Field(default=45.0, ge=5, le=180)
    device: Literal["cpu", "cuda"] = "cpu"
    failure_policy: Literal["fallback", "strict"] = "fallback"
    # Normalized coordinates on the fitted artwork, not on the outer frame.
    subject_box: tuple[float, float, float, float] | None = None
    max_feature_change: float = Field(default=0.10, ge=0, le=0.25)

    @field_validator("subject_box")
    @classmethod
    def valid_box(cls, box):
        if box is not None and not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            raise ValueError("Subject box must be normalized x0,y0,x1,y1 on fitted artwork")
        return box


class DesignConfig(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, validate_default=True
    )
    mode: Literal["multicolor", "lightbox"] = "multicolor"
    colors: int = Field(default=6, ge=1, le=24, strict=True)
    layers: int = Field(default=8, ge=1, le=24, strict=True)
    detail: float = Field(default=0.6, ge=0, le=1)
    palette: list[str] | None = None
    seed: int = Field(default=42, ge=0, le=2**31 - 1, strict=True)
    max_side: int = Field(default=384, ge=16, le=2048, strict=True)
    width_mm: float = Field(default=200, gt=0, le=2000)
    height_mm: float | None = Field(default=None, gt=0, le=2000)
    spacing_mm: float = Field(default=3, ge=0, le=100)
    assembly_mode: Literal["frame_connected", "full_sheet", "mounted_pieces"] = "frame_connected"
    backing: str = "#ffffff"
    frame_mm: float = Field(default=3, gt=0)
    artwork_fit: Literal["contain", "cover"] = "contain"
    workflow: WorkflowSettings | None = None
    ai: AISettings | None = None
    # Persisted physical abstraction scale; absent on legacy projects.
    manufacturing_width_mm: float | None = Field(default=None, gt=0, le=2000)
    abstraction_policy: Literal["cut_aware_v2", "detail_aware_v3"] | None = None
    depth_paper: Literal["white", "multicolor"] | None = None
    detail_policy: Literal["prefer", "strict"] = "prefer"
    detail_boxes: list[tuple[float, float, float, float]] = Field(
        default_factory=list, max_length=16
    )

    @field_validator("detail_boxes")
    @classmethod
    def valid_detail_boxes(cls, boxes):
        if any(not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1) for x0, y0, x1, y1 in boxes):
            raise ValueError("Detail boxes must be normalized rectangles on the fitted artwork")
        return boxes

    @field_validator("backing")
    @classmethod
    def color(cls, value):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Color must be #RRGGBB")
        return value.lower()

    @field_validator("palette")
    @classmethod
    def valid_palette(cls, values):
        if values is None:
            return None
        if not values:
            raise ValueError("Palette cannot be empty")
        return list(dict.fromkeys(cls.color(value) for value in values))

    @model_validator(mode="after")
    def check_palette_limit(self):
        if self.palette and len(self.palette) > self.colors:
            raise ValueError("Palette contains more colors than the color limit")
        if self.workflow and self.workflow.family == "depth_scene" and self.layers < 2:
            raise ValueError("Depth Scene requires a budget of at least two cut sheets")
        return self


def rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def hex_color(value) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in value)


class FabricationProfile(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, validate_default=True
    )
    name: str = "Draft paper profile"
    thickness_mm: float = Field(default=0.25, gt=0, le=10)
    min_feature_width_mm: float = Field(default=1.2, gt=0, le=20)
    min_bridge_width_mm: float = Field(default=1.5, gt=0, le=20)
    min_hole_size_mm: float = Field(default=1, gt=0, le=20)
    cut_tolerance_mm: float = Field(default=0.1, ge=0, le=5)
    method: str = "manual craft knife"
    min_frame_width_mm: float = Field(default=3, gt=0, le=50)
    validation_tolerance_mm: float = Field(default=0.01, gt=0, le=0.1)
    decorative_tip_policy: Literal["bounded_convex", "strict"] = "bounded_convex"


class SnapshotLayer(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(pattern=r"^layer-[0-9]{2}$")
    color: str
    index: int = Field(ge=0, le=23, strict=True)
    spacing_mm: float = Field(ge=0, le=100)
    region_ids: list[int]
    attachment: str | None
    depth_source: (
        Literal[
            "manual",
            "heuristic_vertical_position",
            "model_relative_depth",
            "color_occlusion",
            "tonal_relief",
        ]
        | None
    )
    geometry: dict

    @field_validator("color")
    @classmethod
    def validate_color(cls, value):
        return DesignConfig.color(value)

    @field_validator("region_ids", mode="before")
    @classmethod
    def validate_regions(cls, value):
        if not isinstance(value, list) or any(type(v) is not int or v < 0 for v in value):
            raise ValueError("Region ids must be a list of nonnegative integers")
        return value
