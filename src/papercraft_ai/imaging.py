"""Deterministic, CPU-only image abstraction with inspectable region artifacts."""

import hashlib
import io
import threading
import time
from dataclasses import dataclass
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


def nearest_colors(lab, centers):
    pixels = lab.reshape(-1, 3)
    labels = np.empty(len(pixels), dtype=np.int32)
    for start in range(0, len(pixels), 8192):
        chunk = pixels[start : start + 8192]
        distance = ((chunk[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels[start : start + len(chunk)] = distance.argmin(axis=1)
    return labels.reshape(lab.shape[:2])


def quantize(image, config):
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


def adjacency_graph(regions):
    adjacency = {int(i): set() for i in np.unique(regions)}
    for a, b in ((regions[:-1], regions[1:]), (regions[:, :-1], regions[:, 1:])):
        different = a != b
        pairs = np.unique(np.stack([a[different], b[different]], axis=1), axis=0)
        for first, second in pairs:
            adjacency[int(first)].add(int(second))
            adjacency[int(second)].add(int(first))
    return {key: sorted(value) for key, value in adjacency.items()}


def merge_small_regions(labels, palette, detail, superpixels=None):
    labels = labels.copy()
    min_area = max(1, int(labels.size * 0.002 * (1 - detail) ** 2))
    if min_area <= 1:
        return labels
    regions = connected_regions(labels)
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
        border = ndi.binary_dilation(mask) & ~mask
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


def abstract_image(source, config: DesignConfig | None = None) -> AbstractionResult:
    config = config or DesignConfig()
    start = time.perf_counter()
    original = normalize_image(source, config.max_side, config.backing)
    palette, color_labels = quantize(original, config)
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
    color_labels = merge_small_regions(color_labels, palette, config.detail, superpixels)
    flat = palette[color_labels]
    regions = connected_regions(color_labels)
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
            }
        )
    metrics = visual_metrics(original, flat)
    metrics.update(
        {
            "elapsed_seconds": time.perf_counter() - start,
            "working_array_bytes": int(
                sum(a.nbytes for a in (original, flat, palette, color_labels, regions, superpixels))
            ),
            "region_count": len(info),
            "actual_colors": len(np.unique(color_labels)),
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
    )


def region_overlay(result):
    overlay = result.flat.copy()
    overlay[find_boundaries(result.regions, mode="inner")] = (255, 0, 180)
    return overlay
