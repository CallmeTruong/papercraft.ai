"""Source-registered color filter and explicitly approximate paper transmission."""

import io
import math
import threading
from collections import OrderedDict
from typing import Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from scipy.ndimage import gaussian_filter

from ..geometry import geometry_to_mask
from ..models import DesignError
from .light_zones import LightZone, encode, fit_zones, zone_field
from .models import PrintLayout
from .printing import MM, tile_layout

_CACHE = OrderedDict()
_LOCK = threading.RLock()


class SourceLightPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    mode: Literal["source_color_filter", "uniform_transmission"] = "source_color_filter"
    source_grid_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    paper: Literal["white translucent paper", "colored translucent paper"] = (
        "white translucent paper"
    )
    paper_transmittance_assumption: float = Field(gt=0, le=1)
    diffusion_mm_assumption: float = Field(ge=0, le=30)
    diffuser: Literal["color-printed translucent diffuser", "neutral translucent diffuser"] = (
        "color-printed translucent diffuser"
    )
    diffuser_pdf: Literal["light/source-color-diffuser.pdf"] = "light/source-color-diffuser.pdf"
    light_source: Literal["uniform white backlight"] = "uniform white backlight"
    optical_validation_status: Literal["NOT_RUN"] = "NOT_RUN"
    model: str
    requires: list[str]
    planning_version: Literal[2] | None = None
    zones: list[LightZone] = Field(default_factory=list, max_length=5)
    palette: list[str] = Field(default_factory=list, max_length=5)
    source_distance_mm: float = Field(default=25, ge=5, le=100)
    spread_angle_degrees: float = Field(default=35, ge=5, le=80)
    placement_plane: str = "effective color-source plane behind rear diffuser"
    source_filter: str | None = None

    @model_validator(mode="after")
    def check_zones(self):
        if self.planning_version == 2 and self.mode == "source_color_filter":
            if not self.zones or len({z.id for z in self.zones}) != len(self.zones):
                raise ValueError("A zone plan needs one to five distinct source zones")
            if self.palette != list(dict.fromkeys(z.color for z in self.zones)):
                raise ValueError("Light palette does not match source zones")
        return self


def lighting_plan(design, *, transmittance=0.55, source_distance_mm=25, diffusion_mm=6):
    if not 0 < transmittance <= 1:
        raise DesignError("Invalid paper transmittance assumption")
    zones = fit_zones(design, transmittance, distance=source_distance_mm, diffusion=diffusion_mm)
    return SourceLightPlan.model_validate(
        {
            "mode": "source_color_filter",
            "planning_version": 2,
            "zones": zones,
            "palette": list(dict.fromkeys(zone["color"] for zone in zones)),
            "source_distance_mm": source_distance_mm,
            "spread_angle_degrees": 35,
            "placement_plane": "effective color-source plane behind rear diffuser",
            "source_grid_sha256": design.abstraction.source_hash,
            "paper": "colored translucent paper"
            if design.config.depth_paper == "multicolor"
            else "white translucent paper",
            "paper_transmittance_assumption": transmittance,
            "diffusion_mm_assumption": diffusion_mm,
            "diffuser": "neutral translucent diffuser",
            "source_filter": "broad printed translucent color filter behind neutral diffuser",
            "diffuser_pdf": "light/source-color-diffuser.pdf",
            "light_source": "uniform white backlight",
            "optical_validation_status": "NOT_RUN",
            "model": "five broad Gaussian color zones; bounded NNLS strengths; angular Gaussian spread and per-gap transmission approximation",
            "requires": [
                "color printing on a translucent broad-area source filter",
                "a separate neutral diffuser in front of the source filter",
                "translucent paper for cut sheets",
                "physical color/transmittance calibration",
            ],
        }
    ).model_dump()


def resolved_light_plan(design):
    if (
        design.light_plan
        and design.light_plan.get("planning_version") == 2
        and design.light_plan.get("mode") == "source_color_filter"
    ):
        return design.light_plan
    return lighting_plan(
        design, transmittance=(design.light_plan or {}).get("paper_transmittance_assumption", 0.55)
    )


def uniform_lighting_plan(design, *, transmittance=0.55):
    return SourceLightPlan(
        mode="uniform_transmission",
        paper="colored translucent paper"
        if design.config.depth_paper == "multicolor"
        else "white translucent paper",
        planning_version=2,
        source_distance_mm=25.0,
        spread_angle_degrees=35.0,
        source_grid_sha256=design.abstraction.source_hash,
        paper_transmittance_assumption=transmittance,
        diffusion_mm_assumption=6,
        diffuser="neutral translucent diffuser",
        source_filter=None,
        model="uniform white panel; linear RGB transmission through retained sheets and gaps",
        requires=[
            "translucent paper for cut sheets",
            "neutral diffuser",
            "physical transmittance calibration",
        ],
    ).model_dump()


def overlap_profile(design, *, transmittance=0.55, visible=None):
    if not 0 < transmittance <= 1:
        raise DesignError("Paper transmittance must be within (0,1]")
    side = min(design.config.max_side, 256)
    h = max(1, round(side * design.height_mm / design.width_mm))
    shape = (h, side)
    density = np.zeros(shape, np.uint8)
    active = {layer.id for layer in design.layers} if visible is None else set(visible)
    if active - {layer.id for layer in design.layers}:
        raise DesignError("Unknown visible layer")
    for layer, geometry in zip(design.layers, design.geometries):
        if layer.id in active:
            density += geometry_to_mask(geometry, shape, design.width_mm, design.height_mm)
    window = geometry_to_mask(design.frame.artwork_window, shape, design.width_mm, design.height_mm)
    counts, areas = np.unique(density[window], return_counts=True)
    return {
        "min_sheets": int(counts.min()),
        "colored_upper_bound": design.config.depth_paper == "multicolor",
        "max_sheets": int(counts.max()),
        "bins": [
            {
                "sheet_count": int(n),
                "area_ratio": float(area / max(1, window.sum())),
                "direct_transmission": float(transmittance ** int(n)),
            }
            for n, area in zip(counts, areas)
        ],
        "model": "normal-axis retained-sheet intersections; t^n excludes diffusion/ambient reflection",
    }


def full_sheet_source(
    design,
    *,
    max_side=384,
    high_resolution=False,
    source_distance_mm=None,
    diffusion_mm=None,
    emitter_plane=False,
    linear_output=False,
):
    # high_resolution controls the rendered zone field size, never photographic sampling.
    plan = resolved_light_plan(design)
    w, h = design.width_mm, design.height_mm
    shape = (
        (max(1, round(max_side * h / w)), max_side)
        if w >= h
        else (max_side, max(1, round(max_side * w / h)))
    )
    xs = (np.arange(shape[1]) + 0.5) * w / shape[1]
    ys = (np.arange(shape[0]) + 0.5) * h / shape[0]
    distance = plan["source_distance_mm"] if source_distance_mm is None else source_distance_mm
    diffusion = plan["diffusion_mm_assumption"] if diffusion_mm is None else diffusion_mm
    if not 5 <= distance <= 100 or not 0 <= diffusion <= 30:
        raise DesignError("Source distance must be 5..100 mm and diffusion 0..30 mm")
    field = zone_field(
        plan["zones"],
        xs,
        ys,
        distance=0,
        diffusion=0,
        spread_angle=plan["spread_angle_degrees"],
    )
    if not emitter_plane:
        spread = math.hypot(
            distance * math.tan(math.radians(plan["spread_angle_degrees"]) / 2), diffusion
        )
        # Propagate the actual clipped source-plane field with a normalized kernel.
        # Broadening redistributes light instead of increasing its total energy.
        field = gaussian_filter(
            field, sigma=(spread * shape[0] / h, spread * shape[1] / w, 0), mode="constant", cval=0
        )
    border = design.config.frame_mm
    inside = (
        (xs[None, :] >= border)
        & (xs[None, :] < w - border)
        & (ys[:, None] >= border)
        & (ys[:, None] < h - border)
    )
    result = np.clip(field, 0, 1) if linear_output else encode(field)
    result[~inside] = 1 if linear_output else 255
    return result


def source_light_fields(
    design,
    *,
    transmittance=0.55,
    visible=None,
    source_distance_mm=None,
    diffusion_mm=None,
    uniform_color=None,
    source_gain=1.0,
):
    if not 0 < transmittance <= 1:
        raise DesignError("Paper transmittance must be within (0,1]")
    if not math.isfinite(source_gain) or not 0.01 <= source_gain <= 4096:
        raise DesignError("Source gain must be within0.01..4096")
    ids = {layer.id for layer in design.layers}
    active = ids if visible is None else set(visible)
    if not active <= ids:
        raise DesignError("Unknown light-preview layer")
    from .light_zones import linear_rgb

    if uniform_color is not None:
        from ..models import DesignConfig, rgb

        side = min(design.config.max_side, 512)
        ratio = design.height_mm / design.width_mm
        shape = (
            (max(1, round(side * ratio)), side)
            if ratio <= 1
            else (side, max(1, round(side / ratio)))
        )
        source = np.empty((*shape, 3), np.uint8)
        source[:] = rgb(DesignConfig.color(uniform_color))
        linear = linear_rgb(source)
    else:
        linear = full_sheet_source(
            design,
            max_side=min(design.config.max_side, 512),
            source_distance_mm=source_distance_mm,
            diffusion_mm=diffusion_mm,
            linear_output=True,
        )
    fields = {"diffuser": encode(linear * source_gain)}
    for layer, geometry in reversed(list(zip(design.layers, design.geometries))):
        # Spatial propagation between sheets, followed by local paper attenuation.
        sigma_y = max(0, layer.spacing_mm) * 0.18 * linear.shape[0] / design.height_mm
        sigma_x = max(0, layer.spacing_mm) * 0.18 * linear.shape[1] / design.width_mm
        if sigma_x or sigma_y:
            linear = gaussian_filter(linear, sigma=(sigma_y, sigma_x, 0))
        if layer.id not in active:
            continue
        mask = geometry_to_mask(geometry, linear.shape[:2], design.width_mm, design.height_mm)
        if design.config.depth_paper == "multicolor":
            from ..models import rgb

            # Color increases optical density; it is not a fixed brightness
            # multiplier that survives even at100% nominal transmission.
            density = 1 + 2 * (1 - linear_rgb(np.asarray(rgb(layer.color), dtype=np.uint8)))
            linear *= np.where(mask[..., None], transmittance**density, 1)
        else:
            linear *= np.where(mask, transmittance, 1)[..., None]
        # Apply source power before8-bit encoding. Multiplying an already black
        # texture in the renderer cannot recover weak transmitted light.
        fields[layer.id] = encode(linear * source_gain)
    return fields


def cached_light_fields(
    design,
    *,
    transmittance=0.55,
    visible=None,
    source_distance_mm=None,
    diffusion_mm=None,
    uniform_color=None,
    source_gain=1.0,
):
    key = (
        "light-zones-v3",
        design.fingerprint(),
        source_distance_mm,
        diffusion_mm,
        uniform_color,
        source_gain,
        transmittance,
        tuple(sorted(visible)) if visible is not None else None,
    )
    with _LOCK:
        if key not in _CACHE:
            _CACHE[key] = source_light_fields(
                design,
                transmittance=transmittance,
                visible=visible,
                source_distance_mm=source_distance_mm,
                diffusion_mm=diffusion_mm,
                uniform_color=uniform_color,
                source_gain=source_gain,
            )
        _CACHE.move_to_end(key)
        while len(_CACHE) > 3:
            _CACHE.popitem(last=False)
        return _CACHE[key]


def color_diffuser_pdf(design, layout=None):
    if design.light_plan and design.light_plan.get("mode") == "uniform_transmission":
        raise DesignError("Uniform backlight has no colored source filter to print")
    layout = layout or PrintLayout()
    source = full_sheet_source(design, max_side=2400, high_resolution=True, emitter_plane=True)
    if layout.print_side == "back":
        source = source[:, ::-1].copy()
    reader = ImageReader(Image.fromarray(source))
    stream = io.BytesIO()
    document = canvas.Canvas(stream, pageCompression=1)
    for tile in tile_layout(design.width_mm, design.height_mm, layout):
        pw, ph = tile["paper_width_mm"], tile["paper_height_mm"]
        document.setPageSize((pw * MM, ph * MM))
        margin = layout.margin_mm
        top = margin + 12
        clip = document.beginPath()
        clip.rect(
            margin * MM,
            (ph - top - tile["usable_height_mm"]) * MM,
            tile["usable_width_mm"] * MM,
            tile["usable_height_mm"] * MM,
        )
        document.saveState()
        document.clipPath(clip, stroke=0, fill=0)
        document.drawImage(
            reader,
            (margin - tile["origin_x_mm"]) * MM,
            (ph - top - design.height_mm + tile["origin_y_mm"]) * MM,
            width=design.width_mm * MM,
            height=design.height_mm * MM,
        )
        document.restoreState()
        document.showPage()
    document.save()
    return stream.getvalue()
