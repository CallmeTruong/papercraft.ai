"""Lazy, serialized local inference with bounded per-image caching and explicit provenance."""

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from ..models import DesignError
from .setup import manifest

_LOCK = threading.RLock()
_MODELS = OrderedDict()
_RESULTS = OrderedDict()
_COMPONENT_RESULTS = OrderedDict()
VERSION = "perception-1"


@dataclass
class Perception:
    objects: np.ndarray
    features: np.ndarray
    depth: np.ndarray | None
    diagnostics: list[dict]
    key: str
    source_grid_sha256: str | None = None

    def snapshot(self):
        return {
            "version": VERSION,
            "source_grid_sha256": self.source_grid_sha256,
            "objects": self.objects.tolist(),
            "features": self.features.astype(int).tolist(),
            "depth": self.depth.tolist() if self.depth is not None else None,
            "diagnostics": self.diagnostics,
            "key": self.key,
        }

    @classmethod
    def restore(cls, data, shape, source_hash=None):
        if data.get("version") != VERSION:
            raise DesignError("Unsupported perception artifact version")
        if source_hash is not None and data.get("source_grid_sha256") != source_hash:
            raise DesignError("Perception does not match the fitted source image")
        objects = np.asarray(data["objects"])
        features = np.asarray(data["features"])
        depth = np.asarray(data["depth"], dtype=np.float32) if data["depth"] is not None else None
        if (
            objects.shape != shape
            or features.shape != shape
            or objects.dtype.kind not in "iu"
            or objects.min() < 0
            or objects.max() > 64
            or not np.isin(features, [0, 1]).all()
            or (depth is not None and (depth.shape != shape or not np.isfinite(depth).all()))
        ):
            raise DesignError("Invalid perception artifact grid or values")
        return cls(
            objects.astype(np.int32),
            features.astype(bool),
            depth,
            data["diagnostics"],
            data["key"],
            data.get("source_grid_sha256"),
        )


def cache_identity(settings):
    if not settings:
        return None
    stamps = {}
    for kind in ("segmentation", "depth", "face_features"):
        if getattr(settings, kind):
            try:
                _, data = manifest(kind)
                stamps[kind] = data
            except (OSError, ValueError, KeyError):
                stamps[kind] = "unavailable"
    return {"version": VERSION, "settings": settings.model_dump(), "models": stamps}


def load(kind, device):
    directory, info = manifest(kind, verify=True)
    identity = hashlib.sha256(str(info).encode()).hexdigest()
    key = (kind, device, identity)
    if key not in _MODELS:
        # A complete per-image proposal is cached separately. Keep one loaded network to
        # avoid holding three model weight sets on an 8 GB workstation.
        import gc

        while _MODELS:
            _, previous = _MODELS.popitem(last=False)
            if hasattr(previous, "close"):
                previous.close()
            del previous
        gc.collect()
        import psutil

        available = psutil.virtual_memory().available
        reserve_mb = 192 if kind == "face_features" else 768
        if available < reserve_mb * 1024**2:
            raise MemoryError(
                f"Not enough free RAM for {kind}: {available // 1024**2} MB available; reserve at least {reserve_mb} MB"
            )
        if kind == "face_features":
            import mediapipe as mp

            model = mp.tasks.vision.FaceLandmarker.create_from_options(
                mp.tasks.vision.FaceLandmarkerOptions(
                    base_options=mp.tasks.BaseOptions(
                        model_asset_path=str(directory / "face_landmarker.task")
                    ),
                    num_faces=4,
                )
            )
        else:
            import torch
            from transformers import pipeline

            if device == "cuda" and not torch.cuda.is_available():
                raise RuntimeError("CUDA requested but not available")
            torch.set_num_threads(min(4, torch.get_num_threads()))
            model = pipeline(
                "mask-generation" if kind == "segmentation" else "depth-estimation",
                model=str(directory),
                device=0 if device == "cuda" else -1,
            )
        _MODELS[key] = model
        while len(_MODELS) > 1:
            _, old = _MODELS.popitem(last=False)
            if hasattr(old, "close"):
                old.close()
    else:
        _MODELS.move_to_end(key)
    return _MODELS[key], {
        "model_id": info["model_id"],
        "revision": info["revision"],
        "manifest_sha256": identity,
        "device": "cpu" if kind == "face_features" else device,
    }


def infer_component(kind, image, settings):
    model, provenance = load(kind, settings.device)
    if kind == "segmentation":
        # One image encoding, bounded automatic prompts; no semantic label claims.
        if settings.subject_box:
            # Prompted box uses the pipeline's already loaded model/processor.
            import torch
            from transformers import Sam2Processor

            processor = Sam2Processor(model.image_processor)
            w, h = image.size
            x0, y0, x1, y1 = settings.subject_box
            inputs = processor(
                images=image, input_boxes=[[[x0 * w, y0 * h, x1 * w, y1 * h]]], return_tensors="pt"
            ).to(model.device)
            with torch.inference_mode():
                output = model.model(**inputs)
            masks = processor.post_process_masks(
                output.pred_masks.cpu(), inputs["original_sizes"].cpu()
            )[0][0]
            best = int(output.iou_scores[0, 0].argmax())
            masks = [masks[best].numpy()]
        else:
            masks = model(
                image,
                points_per_crop=8,
                points_per_batch=8,
                pred_iou_thresh=0.88,
                stability_score_thresh=0.95,
            )["masks"]
        masks = [np.asarray(m, dtype=bool) for m in masks]
        return masks, provenance
    if kind == "depth":
        # predicted_depth contains raw relative inverse depth; larger means nearer.
        result = model(image)["predicted_depth"]
        return np.asarray(result.squeeze().cpu(), dtype=np.float32), provenance
    import mediapipe as mp

    result = model.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.asarray(image)))
    features = np.zeros((image.height, image.width), np.uint8)
    groups = (
        [33, 160, 158, 133, 153, 144],
        [362, 385, 387, 263, 373, 380],
        [61, 40, 0, 270, 291, 321, 17, 91],
        [168, 98, 2, 327],
    )
    for face in result.face_landmarks:
        for group in groups:
            pts = np.array(
                [[round(face[i].x * image.width), round(face[i].y * image.height)] for i in group],
                np.int32,
            )
            cv2.fillConvexPoly(features, cv2.convexHull(pts), 1)
    features = cv2.dilate(features, np.ones((3, 3), np.uint8)) > 0
    return features, provenance


def component_inference(kind, original, settings, identity):
    from ..pipeline import digest

    key = digest(
        {
            "image": hashlib.sha256(original.tobytes()).hexdigest(),
            "version": VERSION,
            "component": kind,
            "model": identity["models"].get(kind),
            "device": settings.device,
            "box": settings.subject_box if kind == "segmentation" else None,
        }
    )
    if key in _COMPONENT_RESULTS:
        _COMPONENT_RESULTS.move_to_end(key)
        output, provenance = _COMPONENT_RESULTS[key]
        return output, provenance, True
    output, provenance = infer_component(kind, Image.fromarray(original), settings)
    if isinstance(output, list):
        output = sorted(output, key=np.sum, reverse=True)[:64]
        size = sum(value.nbytes for value in output)
    else:
        size = output.nbytes
    if size <= 16 * 1024**2:
        _COMPONENT_RESULTS[key] = (output, provenance)
        while len(_COMPONENT_RESULTS) > 4:
            _COMPONENT_RESULTS.popitem(last=False)
    return output, provenance, False


def analyze(original, settings):
    from ..pipeline import digest

    shape = original.shape[:2]
    identity = cache_identity(settings)
    key = digest({"image": hashlib.sha256(original.tobytes()).hexdigest(), "identity": identity})
    with _LOCK:
        if key in _RESULTS:
            _RESULTS.move_to_end(key)
            return _RESULTS[key]
        objects = np.zeros(shape, np.int32)
        features = np.zeros(shape, bool)
        depth = None
        diagnostics = []
        for kind in ("segmentation", "face_features", "depth"):
            if not getattr(settings, kind):
                continue
            started = time.perf_counter()
            try:
                output, provenance, cache_hit = component_inference(
                    kind, original, settings, identity
                )
                if kind == "segmentation":
                    masks = []
                    for mask in output:
                        if mask.shape != shape:
                            raise ValueError("Segmentation grid does not match fitted image")
                        if 0.001 <= mask.mean() <= 0.95:
                            masks.append(mask)
                    # Smaller masks win overlap, each pixel has exactly one owner.
                    for index, mask in enumerate(sorted(masks, key=np.sum, reverse=True)[:64], 1):
                        objects[mask] = index
                    if not objects.any():
                        raise ValueError("No usable object masks")
                elif kind == "face_features":
                    if output.shape != shape:
                        raise ValueError("Feature grid mismatch")
                    features = output.astype(bool)
                else:
                    if output.ndim != 2 or not np.isfinite(output).all():
                        raise ValueError("Invalid relative-depth map")
                    aligned = cv2.resize(
                        output, (shape[1], shape[0]), interpolation=cv2.INTER_LINEAR
                    )
                    low, high = np.percentile(aligned, [2, 98])
                    if high - low <= 1e-6:
                        raise ValueError("Relative depth is constant or ambiguous")
                    depth = np.clip((aligned - low) / (high - low), 0, 1).astype(np.float32)
                diagnostics.append(
                    {
                        "component": kind,
                        "status": "applied"
                        if kind != "face_features" or features.any()
                        else "no_detection",
                        **provenance,
                        "inference_cache_hit": cache_hit,
                        "elapsed_seconds": time.perf_counter() - started,
                    }
                )
            except (
                ImportError,
                OSError,
                RuntimeError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                MemoryError,
            ) as exc:
                retryable = isinstance(exc, MemoryError) or "out of memory" in str(exc).lower()
                if settings.failure_policy == "strict":
                    raise DesignError(
                        f"{kind}: {exc}",
                        "RESOURCE_LIMIT" if retryable else "CAPABILITY_UNAVAILABLE",
                    ) from exc
                diagnostics.append(
                    {
                        "component": kind,
                        "status": "fallback",
                        "reason": str(exc),
                        "retryable": retryable,
                        "elapsed_seconds": time.perf_counter() - started,
                    }
                )
        result = Perception(
            objects,
            features,
            depth,
            diagnostics,
            key,
            hashlib.sha256(original.tobytes()).hexdigest(),
        )
        for array in (objects, features, depth):
            if array is not None:
                array.setflags(write=False)
        if not any(item.get("retryable") for item in diagnostics):
            _RESULTS[key] = result
        while len(_RESULTS) > 4:
            _RESULTS.popitem(last=False)
        return result


def propose_depths(result, count):
    perception = result.perception
    if perception is None or perception.depth is None:
        return None
    values = {}
    # Object median is shared by all its color regions. Background uses region median.
    medians = {
        int(i): float(np.median(perception.depth[perception.objects == i]))
        for i in np.unique(perception.objects)
        if i
    }
    for region in result.region_info:
        obj = region.get("object_id", 0)
        if obj:
            near = medians[obj]
        else:
            x0, y0, x1, y1 = region["bbox_px"]
            mask = result.regions[y0:y1, x0:x1] == region["id"]
            near = float(np.median(perception.depth[y0:y1, x0:x1][mask]))
        # Frontmost sheet index is zero, larger inverse depth means nearer.
        values[region["id"]] = min(count - 1, int((1 - near) * count))
    return values
