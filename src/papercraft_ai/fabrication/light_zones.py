"""Low-frequency effective color sources; no image pixels are used in rendered fields."""

import math

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator
from scipy.cluster.vq import kmeans2
from scipy.ndimage import gaussian_filter
from scipy.optimize import lsq_linear

from ..geometry import geometry_to_mask
from ..models import DesignConfig, hex_color, rgb


class LightZone(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    id: str
    color: str
    x_mm: float = Field(gt=0)
    y_mm: float = Field(gt=0)
    sigma_x_mm: float = Field(ge=8)
    sigma_y_mm: float = Field(ge=8)
    intensity: float = Field(ge=0, le=1.8)

    @field_validator("color")
    @classmethod
    def valid_color(cls, value):
        return DesignConfig.color(value)


def linear_rgb(image):
    value = np.asarray(image, dtype=np.float64) / 255
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def encode(value):
    value = np.clip(value, 0, 1)
    srgb = np.where(value <= 0.0031308, value * 12.92, 1.055 * value ** (1 / 2.4) - 0.055)
    return np.uint8(np.clip(np.rint(srgb * 255), 0, 255))


def zone_field(zones, xs, ys, *, distance=25, diffusion=6, spread_angle=35):
    propagation = distance * math.tan(math.radians(spread_angle) / 2)
    field = np.zeros((len(ys), len(xs), 3), np.float64)
    for raw in zones:
        zone = LightZone.model_validate(raw)
        sx = math.sqrt(zone.sigma_x_mm**2 + propagation**2 + diffusion**2)
        sy = math.sqrt(zone.sigma_y_mm**2 + propagation**2 + diffusion**2)
        weight = np.exp(
            -0.5 * (((xs[None, :] - zone.x_mm) / sx) ** 2 + ((ys[:, None] - zone.y_mm) / sy) ** 2)
        )
        energy_scale = zone.sigma_x_mm * zone.sigma_y_mm / (sx * sy)
        field += weight[..., None] * linear_rgb(rgb(zone.color)) * zone.intensity * energy_scale
    return np.clip(field, 0, 1)


def fit_zones(design, transmittance, *, distance=25, diffusion=6):
    border = design.config.frame_mm
    width, height = design.width_mm - 2 * border, design.height_mm - 2 * border
    image = np.asarray(
        Image.fromarray(design.abstraction.original).resize((32, 32), Image.Resampling.BOX)
    )
    coarse = linear_rgb(image)
    target = gaussian_filter(coarse, sigma=(2.5, 2.5, 0))
    yy, xx = np.indices((32, 32))
    # Color plus low-weight spatial layout; five bounded clusters maximum.
    features = np.concatenate(
        [target.reshape(-1, 3), xx.reshape(-1, 1) / 32 * 0.5, yy.reshape(-1, 1) / 32 * 0.5],
        axis=1,
    )
    count = min(5, max(1, len(np.unique(np.round(target.reshape(-1, 3), 2), axis=0))))
    _, labels = kmeans2(features, count, minit="++", seed=42, iter=20)
    zones = []
    for index in range(count):
        mask = labels.reshape(32, 32) == index
        if mask.mean() < 0.035:
            continue
        samples = coarse[mask]
        chroma = samples.max(axis=1) - samples.min(axis=1)
        color = np.average(samples, axis=0, weights=(chroma + 0.005) ** 3)
        # Preserve chromaticity; scalar brightness is solved separately below.
        color = color / max(float(color.max()), 0.15)
        y, x = np.where(mask)
        zones.append(
            LightZone(
                id=f"zone-{len(zones) + 1:02}",
                color=hex_color(encode(color)),
                x_mm=border + (float(x.mean()) + 0.5) * width / 32,
                y_mm=border + (float(y.mean()) + 0.5) * height / 32,
                sigma_x_mm=max(
                    8, min(width * 0.32, max(width * 0.12, float(x.std()) * width / 32 * 1.25))
                ),
                sigma_y_mm=max(
                    8, min(height * 0.32, max(height * 0.12, float(y.std()) * height / 32 * 1.25))
                ),
                intensity=1,
            ).model_dump()
        )
    # Share near-identical emitter colors while keeping distinct physical centers.
    # This avoids a palette full of barely distinguishable shades from spatial clusters.
    palette = []
    for zone in zones:
        match = next(
            (
                color
                for color in palette
                if np.linalg.norm(
                    np.asarray(rgb(color), float) - np.asarray(rgb(zone["color"]), float)
                )
                < 38
            ),
            None,
        )
        if match is None:
            palette.append(zone["color"])
        else:
            zone["color"] = match
    xs = border + (np.arange(32) + 0.5) * width / 32
    ys = border + (np.arange(32) + 0.5) * height / 32
    density = np.zeros((32, 32), float)
    from shapely.affinity import translate

    for geometry in design.geometries:
        density += geometry_to_mask(translate(geometry, -border, -border), (32, 32), width, height)
    transmission = transmittance**density
    # Fit low-frequency target with bounded compensation for assumed paper losses.
    # This is not a calibration of real LED lux, ink transmission or paper scattering.
    basis = np.stack(
        [
            (
                zone_field([z], xs, ys, distance=distance, diffusion=diffusion)
                * transmission[..., None]
            ).reshape(-1)
            for z in zones
        ],
        axis=1,
    )
    desired = (target * np.maximum(transmission[..., None], 0.35)).reshape(-1)
    saturation = np.array([np.ptp(linear_rgb(rgb(z["color"]))) for z in zones])
    chromatic = saturation > 0.15
    lower = np.where(chromatic, 0.18, 0)
    upper = np.where(chromatic, 1.8, 0.35 if chromatic.any() else 1.8)
    strengths = lsq_linear(
        np.vstack([basis, np.eye(len(zones)) * 0.08]),
        np.r_[desired, np.zeros(len(zones))],
        bounds=(lower, upper),
    ).x
    for zone, strength in zip(zones, strengths):
        zone["intensity"] = float(np.clip(strength, 0, 1.8))
    return [zone for zone in zones if zone["intensity"] > 0.02] or zones[:1]
