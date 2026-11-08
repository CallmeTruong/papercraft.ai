"""Source-registered color filter and explicitly approximate paper transmission."""

import io
import threading
from collections import OrderedDict
from typing import Literal

import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from scipy.ndimage import gaussian_filter

from ..geometry import geometry_to_mask
from ..imaging import normalize_image
from ..models import DesignError
from .models import PrintLayout
from .printing import MM, tile_layout

_CACHE = OrderedDict()
_LOCK = threading.RLock()


class SourceLightPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    mode: Literal["source_color_filter"] = "source_color_filter"
    source_grid_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    paper: Literal["white translucent paper"] = "white translucent paper"
    paper_transmittance_assumption: float = Field(gt=0, le=1)
    diffusion_mm_assumption: float = Field(ge=0, le=10)
    diffuser: Literal["color-printed translucent diffuser"] = "color-printed translucent diffuser"
    diffuser_pdf: Literal["light/source-color-diffuser.pdf"] = "light/source-color-diffuser.pdf"
    light_source: Literal["uniform white backlight"] = "uniform white backlight"
    optical_validation_status: Literal["NOT_RUN"] = "NOT_RUN"
    model: str
    requires: list[str]


def lighting_plan(design, *, transmittance=0.55):
    if not 0 < transmittance <= 1:
        raise DesignError("Invalid paper transmittance assumption")
    return {
        "mode": "source_color_filter",
        "source_grid_sha256": design.abstraction.source_hash,
        "paper": "white translucent paper",
        "paper_transmittance_assumption": transmittance,
        "diffusion_mm_assumption": 0.6,
        "diffuser": "color-printed translucent diffuser",
        "diffuser_pdf": "light/source-color-diffuser.pdf",
        "light_source": "uniform white backlight",
        "optical_validation_status": "NOT_RUN",
        "model": "linear RGB filter times paper transmittance per retained sheet",
        "requires": [
            "color printing on a translucent diffuser",
            "translucent paper for cut sheets",
            "physical color/transmittance calibration",
        ],
    }


def full_sheet_source(design, *, max_side=384, high_resolution=False):
    w, h = design.width_mm, design.height_mm
    shape = (
        (max(1, round(max_side * h / w)), max_side)
        if w >= h
        else (max_side, max(1, round(max_side * w / h)))
    )
    source = design.abstraction.original
    if high_resolution:
        source = normalize_image(
            design.source_bytes, max_side=max_side, backing=design.config.backing
        )
        aspect = (w - 2 * design.config.frame_mm) / (h - 2 * design.config.frame_mm)
        target = (
            (max_side, max(1, round(max_side / aspect)))
            if aspect >= 1
            else (max(1, round(max_side * aspect)), max_side)
        )
        image = Image.fromarray(source)
        source = np.asarray(
            ImageOps.fit(image, target)
            if design.config.artwork_fit == "cover"
            else ImageOps.pad(image, target, color=design.config.backing)
        )
    ys = (np.arange(shape[0]) + 0.5) * h / shape[0]
    xs = (np.arange(shape[1]) + 0.5) * w / shape[1]
    border = design.config.frame_mm
    inside = (
        (ys[:, None] >= border)
        & (ys[:, None] < h - border)
        & (xs[None, :] >= border)
        & (xs[None, :] < w - border)
    )
    sx = np.clip(
        ((xs - border) * source.shape[1] / (w - 2 * border)).astype(int), 0, source.shape[1] - 1
    )
    sy = np.clip(
        ((ys - border) * source.shape[0] / (h - 2 * border)).astype(int), 0, source.shape[0] - 1
    )
    result = np.full((*shape, 3), 255, np.uint8)
    pixels = source[sy[:, None], sx[None, :]]
    result[inside] = pixels[inside]
    return result


def source_light_fields(design, *, transmittance=0.55, visible=None):
    if not 0 < transmittance <= 1:
        raise DesignError("Paper transmittance must be within (0,1]")
    ids = {layer.id for layer in design.layers}
    active = ids if visible is None else set(visible)
    if not active <= ids:
        raise DesignError("Unknown light-preview layer")
    source = full_sheet_source(design, max_side=min(design.config.max_side, 512))
    srgb = source.astype(np.float32) / 255
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    sigma = 0.6 * source.shape[1] / design.width_mm
    linear = gaussian_filter(linear, sigma=(sigma, sigma, 0))

    def encode(value):
        encoded = np.where(
            value <= 0.0031308, value * 12.92, 1.055 * np.maximum(value, 0) ** (1 / 2.4) - 0.055
        )
        return np.uint8(np.clip(np.rint(encoded * 255), 0, 255))

    fields = {"diffuser": encode(linear)}
    density = np.zeros(source.shape[:2], np.uint8)
    for layer, geometry in reversed(list(zip(design.layers, design.geometries))):
        if layer.id not in active:
            continue
        density += geometry_to_mask(geometry, density.shape, design.width_mm, design.height_mm)
        fields[layer.id] = encode(linear * np.power(transmittance, density)[..., None])
    return fields


def cached_light_fields(design, *, transmittance=0.55, visible=None):
    key = (
        design.fingerprint(),
        transmittance,
        tuple(sorted(visible)) if visible is not None else None,
    )
    with _LOCK:
        if key not in _CACHE:
            _CACHE[key] = source_light_fields(design, transmittance=transmittance, visible=visible)
        _CACHE.move_to_end(key)
        while len(_CACHE) > 3:
            _CACHE.popitem(last=False)
        return _CACHE[key]


def color_diffuser_pdf(design, layout=None):
    layout = layout or PrintLayout()
    source = full_sheet_source(design, max_side=2400, high_resolution=True)
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
