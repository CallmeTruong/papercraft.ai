"""Shared mode contract. Style generation and fabrication are separate capabilities."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StyleOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    polarity: Literal["dark_paper", "light_paper"] = "dark_paper"
    composition: Literal["window_mat", "frame_flow"] = "window_mat"
    background: Literal["preserve", "remove"] = "preserve"


class WorkflowSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workflow_schema: Literal[1] = 1
    family: Literal["stencil", "multicolor", "depth_scene"] = "multicolor"
    style: Literal["bold", "detailed", "colorblock", "splash_portrait", "landscape", "portal"] = (
        "colorblock"
    )
    assembly: Literal["flat", "spaced"] = "flat"
    lighting: Literal["ambient", "backlit"] = "ambient"
    style_options: StyleOptions = Field(default_factory=StyleOptions)

    @model_validator(mode="after")
    def compatible(self):
        matrix = {
            "stencil": {"bold", "detailed"},
            "multicolor": {"colorblock", "splash_portrait"},
            "depth_scene": {"landscape", "portal"},
        }
        if self.style not in matrix[self.family]:
            raise ValueError("Style is incompatible with the selected family")
        if self.family == "depth_scene" and self.assembly != "spaced":
            raise ValueError("Depth Scene requires spaced assembly")
        return self


def settings(config):
    return config.workflow or (
        WorkflowSettings(
            family="depth_scene", style="landscape", assembly="spaced", lighting="backlit"
        )
        if config.mode == "lightbox"
        else WorkflowSettings(assembly="spaced" if config.spacing_mm > 0 else "flat")
    )


def capabilities():
    return {
        "families": ["stencil", "multicolor", "depth_scene"],
        "stencil": "cpu_threshold_candidates",
        "colorblock": "cpu_palette_regions",
        "splash_portrait": "requires_authored_style_target_or_scene",
        "portal": "requires_authored_scene",
        "semantic_masks": "unavailable: local MobileSAM adapter not integrated",
        "relative_depth": "unavailable: local depth model not integrated",
        "generative_style": "unavailable: no image model configured",
    }
