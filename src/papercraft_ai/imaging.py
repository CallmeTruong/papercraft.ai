"""Deterministic, CPU-only image abstraction with inspectable region artifacts."""

import hashlib
import io
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from scipy import ndimage as ndi
from skimage.color import deltaE_ciede2000, rgb2lab
from skimage.segmentation import find_boundaries, slic

from .models import DesignConfig, DesignError, rgb

_KMEANS_LOCK = threading.Lock()


def normalize_image(source, max_side=384, backing="#ffffff") -> np.ndarray:
    try:
        if isinstance(source, np.ndarray):
            if source.dtype != np.uint8 or source.ndim != 3 or source.shape[2] not in (3, 4):
                raise DesignError("Input image must be uint8 RGB or RGBA")
            image = Image.fromarray(source.copy())
        else:
            data = Path(source).read_bytes() if isinstance(source, (str, Path)) else source
            image = Image.open(io.BytesIO(data))
        if image.width * image.height > 40_000_000:
            raise DesignError("Input image exceeds 40 megapixels", "RESOURCE_LIMIT")
        image = ImageOps.exif_transpose(image).convert("RGBA")
        base = Image.new("RGBA", image.size, (*rgb(backing), 255))
        image = Image.alpha_composite(base, image).convert("RGB")
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        result = np.array(image, dtype=np.uint8)
        result.setflags(write=False)
        return result
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise DesignError("Cannot read input image") from exc


@dataclass(frozen=True)
class AbstractionResult:
    original: np.ndarray
    flat: np.ndarray
    palette: np.ndarray
    color_labels: np.ndarray
    regions: np.ndarray
    region_info: list[dict]
    adjacency: dict[int, list[int]]
    superpixels: np.ndarray
    source_hash: str
    metrics: dict
    perception: object | None = None


def nearest_colors(lab, centers):
    pixels = lab.reshape(-1, 3)
    labels = np.empty(len(pixels), dtype=np.int32)
    for start in range(0, len(pixels), 8192):
        chunk = pixels[start : start + 8192]
        distance = ((chunk[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels[start : start + len(chunk)] = distance.argmin(axis=1)
    return labels.reshape(lab.shape[:2])


def quantize(image, config, features=None):
    if config.manufacturing_width_mm is not None:
        # Flatten texture before palette fitting; preserve the original for quality metrics.
        image = cv2.pyrMeanShiftFiltering(image, sp=5 + 5 * (1 - config.detail), sr=25)
    # A tiny spatial kernel avoids erasing one-pixel details at detail=1.
    smooth = (
        image.copy()
        if config.detail == 1
        else cv2.bilateralFilter(
            image, 5, 10 + 35 * (1 - config.detail), 2 + 4 * (1 - config.detail)
        )
    )
    lab = cv2.cvtColor(smooth.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    if config.palette:
        palette = np.array([rgb(c) for c in config.palette], dtype=np.uint8)
    else:
        unique = np.unique(smooth.reshape(-1, 3), axis=0)
        if len(unique) <= config.colors:
            palette = unique
        else:
            rng = np.random.default_rng(config.seed)
            sample = lab.reshape(-1, 3)
            if len(sample) > 16000:
                sample = sample[rng.choice(len(sample), 16000, replace=False)]
            if features is not None and features.any():
                # Reserve a quarter of fitting samples for small identity-relevant areas.
                # This changes palette fitting only; custom paper palettes remain authoritative.
                local = lab[features]
                reserved = min(4000, max(32, len(sample) // 4))
                sample = np.concatenate(
                    [
                        sample[: max(1, len(sample) - reserved)],
                        local[rng.choice(len(local), reserved, replace=True)],
                    ]
                )
            sample = np.ascontiguousarray(sample, dtype=np.float32)
            with _KMEANS_LOCK:
                cv2.setRNGSeed(config.seed)
                _, _, centers = cv2.kmeans(
                    sample,
                    config.colors,
                    None,
                    (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 0.05),
                    1,
                    cv2.KMEANS_PP_CENTERS,
                )
            converted = cv2.cvtColor(centers[None, :, :], cv2.COLOR_LAB2RGB)[0]
            palette = np.unique(np.clip(np.rint(converted * 255), 0, 255).astype(np.uint8), axis=0)
    centers = cv2.cvtColor(palette[None].astype(np.float32) / 255, cv2.COLOR_RGB2LAB)[0]
    return palette, nearest_colors(lab, centers)


def connected_regions(color_labels):
    labels = np.full(color_labels.shape, -1, np.int32)
    offset = 0
    for color in np.unique(color_labels):
        components, count = ndi.label(color_labels == color)
        selected = components > 0
        labels[selected] = components[selected] - 1 + offset
        offset += count
    return labels


def guided_regions(labels, perception=None):
    if perception is None or not perception.objects.any():
        return connected_regions(labels)
    # Object boundaries survive even where the two objects have the same paper color.
    _, joint = np.unique(
        np.stack([labels.ravel(), perception.objects.ravel()], axis=1), axis=0, return_inverse=True
    )
    return connected_regions(joint.reshape(labels.shape))


def adjacency_graph(regions):
    adjacency = {int(i): set() for i in np.unique(regions)}
    for a, b in ((regions[:-1], regions[1:]), (regions[:, :-1], regions[:, 1:])):
        different = a != b
        pairs = np.unique(np.stack([a[different], b[different]], axis=1), axis=0)
        for first, second in pairs:
            adjacency[int(first)].add(int(second))
            adjacency[int(second)].add(int(first))
    return {key: sorted(value) for key, value in adjacency.items()}


def merge_small_regions(labels, palette, detail, superpixels=None, perception=None):
    labels = labels.copy()
    min_area = max(1, int(labels.size * 0.002 * (1 - detail) ** 2))
    if min_area <= 1:
        return labels
    regions = guided_regions(labels, perception)
    areas = np.bincount(regions.ravel())
    objects = ndi.find_objects(regions + 1)
    palette_lab = rgb2lab(palette[None])[0]
    for region_id in np.argsort(areas):
        if areas[region_id] >= min_area:
            break
        sy, sx = objects[region_id]
        sy = slice(max(0, sy.start - 1), min(labels.shape[0], sy.stop + 1))
        sx = slice(max(0, sx.start - 1), min(labels.shape[1], sx.stop + 1))
        local = labels[sy, sx]
        mask = regions[sy, sx] == region_id
        if perception is not None and np.any(perception.features[sy, sx] & mask):
            continue
        border = ndi.binary_dilation(mask) & ~mask
        if perception is not None:
            owner = perception.objects[sy, sx][mask][0]
            border &= perception.objects[sy, sx] == owner
        if not border.any():
            continue
        # Four-neighbor dilation already consists exactly of adjacent original regions.
        neighbors, counts = np.unique(local[border], return_counts=True)
        original = local[mask][0]
        # Border agreement favors spatially continuous material over unrelated global colors.
        distances = ((palette_lab[neighbors] - palette_lab[original]) ** 2).sum(axis=1)
        continuity = np.ones(len(neighbors))
        if superpixels is not None:
            local_superpixels = superpixels[sy, sx]
            shared = np.isin(local_superpixels, np.unique(local_superpixels[mask])) & border
            continuity += np.array(
                [np.count_nonzero(shared & (local == neighbor)) for neighbor in neighbors]
            )
        score = distances / ((counts + 1) * continuity)
        local[mask] = neighbors[score.argmin()]
    return labels


def visual_metrics(original, flat):
    original_lab = rgb2lab(original)
    flat_lab = rgb2lab(flat)
    delta = deltaE_ciede2000(original_lab, flat_lab)
    first = cv2.Canny(original, 60, 120) > 0
    second = cv2.Canny(flat, 60, 120) > 0
    if not first.any() and not second.any():
        score = 1.0
    elif not first.any() or not second.any():
        score = 0.0
    else:
        precision = (second & ndi.binary_dilation(first)).sum() / second.sum()
        recall = (first & ndi.binary_dilation(second)).sum() / first.sum()
        score = 2 * precision * recall / max(precision + recall, 1e-12)
    return {
        "delta_e_mean": float(delta.mean()),
        "boundary_f_score": float(score),
        "pixel_error_mean": float(np.abs(original.astype(float) - flat).mean() / 255),
        "region_preservation": None,
        "region_preservation_status": "ANNOTATIONS_REQUIRED",
    }


def abstract_image(
    source, config: DesignConfig | None = None, *, perception=None
) -> AbstractionResult:
    config = config or DesignConfig()
    start = time.perf_counter()
    original = normalize_image(source, config.max_side, config.backing)
    if isinstance(perception, dict):
        from .ai.perception import Perception

        perception = Perception.restore(
            perception, original.shape[:2], hashlib.sha256(original.tobytes()).hexdigest()
        )
    elif config.ai and any((config.ai.segmentation, config.ai.depth, config.ai.face_features)):
        from .ai.perception import analyze
        from .workflows import settings

        unused_depth = config.ai.depth and settings(config).family != "depth_scene"
        effective_ai = config.ai.model_copy(update={"depth": False}) if unused_depth else config.ai
        perception = analyze(original, effective_ai)
        if unused_depth:
            perception = replace(
                perception,
                diagnostics=[
                    *perception.diagnostics,
                    {
                        "component": "depth",
                        "status": "not_applicable",
                        "elapsed_seconds": 0,
                        "reason": "Relative-depth inference is only used for Depth Scene",
                    },
                ],
            )
    palette, color_labels = quantize(
        original, config, perception.features if perception is not None else None
    )
    if perception is not None and perception.features.any():
        # Avoid bilateral smoothing of protected features; retain the same finite paper palette.
        raw_lab = cv2.cvtColor(original.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
        centers = cv2.cvtColor(palette[None].astype(np.float32) / 255, cv2.COLOR_RGB2LAB)[0]
        raw_labels = nearest_colors(raw_lab, centers)
        color_labels[perception.features] = raw_labels[perception.features]
    h, w = original.shape[:2]
    superpixels = slic(
        original,
        n_segments=max(4, int(h * w / (30 + 150 * (1 - config.detail)))),
        compactness=8,
        start_label=0,
        enforce_connectivity=True,
        channel_axis=-1,
    )
    # Merge on the region-adjacency graph, using SLIC continuity as a spatial prior.
    # Keep quantized boundaries rather than replacing a whole superpixel with one color.
    color_labels = merge_small_regions(
        color_labels, palette, config.detail, superpixels, perception
    )
    from .fabrication.abstraction import manufacturable_labels

    color_labels = manufacturable_labels(color_labels, config, perception)
    flat = palette[color_labels]
    regions = guided_regions(color_labels, perception)
    adjacency = adjacency_graph(regions)
    info = []
    areas = np.bincount(regions.ravel())
    objects = ndi.find_objects(regions + 1)
    for region_id, slices in enumerate(objects):
        sy, sx = slices
        local = regions[sy, sx] == region_id
        first = np.flatnonzero(local)[0]
        info.append(
            {
                "id": region_id,
                "area_px": int(areas[region_id]),
                "color_index": int(color_labels[sy, sx].ravel()[first]),
                "bbox_px": [sx.start, sy.start, sx.stop, sy.stop],
                **(
                    {"object_id": int(perception.objects[sy, sx].ravel()[first])}
                    if perception is not None
                    else {}
                ),
            }
        )
    metrics = visual_metrics(original, flat)
    if perception is not None:
        metrics["ai_diagnostics"] = perception.diagnostics
        metrics["ai_analysis_key"] = perception.key
        metrics["important_feature_pixels"] = int(perception.features.sum())
        if perception.features.any():
            metrics["feature_pixel_error_mean"] = float(
                np.abs(
                    original[perception.features].astype(float) - flat[perception.features]
                ).mean()
                / 255
            )
    metrics.update(
        {
            "elapsed_seconds": time.perf_counter() - start,
            "working_array_bytes": int(
                sum(a.nbytes for a in (original, flat, palette, color_labels, regions, superpixels))
            ),
            "region_count": len(info),
            "actual_colors": len(np.unique(color_labels)),
            "manufacturing_width_mm": config.manufacturing_width_mm,
        }
    )
    for array in (flat, palette, color_labels, regions, superpixels):
        array.setflags(write=False)
    return AbstractionResult(
        original,
        flat,
        palette,
        color_labels,
        regions,
        info,
        adjacency,
        superpixels,
        hashlib.sha256(original.tobytes()).hexdigest(),
        metrics,
        perception,
    )


def region_overlay(result):
    overlay = result.flat.copy()
    overlay[find_boundaries(result.regions, mode="inner")] = (255, 0, 180)
    return overlay
