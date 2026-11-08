"""Revisioned CPU pipeline; canonical polygons feed validation, preview and export."""

import base64
import hashlib
import io
import json
import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from uuid import uuid4

import numpy as np
from PIL import Image, ImageOps
from shapely.affinity import scale
from shapely.errors import GEOSException
from shapely.geometry import mapping, shape

from .fabrication.frame import build_frame, material_from_mask
from .fabrication.models import FrameSpec, ScenePlan
from .fabrication.scene import check_scene_order, compile_scene, protected_geometry, scene_index
from .geometry import (
    CHECKER_VERSION,
    geometry_to_mask,
    repair_geometry,
    validate_geometry,
)
from .imaging import AbstractionResult, abstract_image
from .layers import PaperLayer, composite_layers, construct_layers, layer_metrics
from .models import DesignConfig, DesignError, FabricationProfile, SnapshotLayer
from .workflows import settings

_IMAGE_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()


def clear_image_cache():
    with _CACHE_LOCK:
        _IMAGE_CACHE.clear()


def cached_abstraction(source, config):
    # Only actual image-stage dependencies; fitting changes the source bytes before this key.
    key = digest(
        {
            "version": "abstraction-v2",
            "source": hashlib.sha256(source).hexdigest(),
            **{
                k: config.model_dump()[k]
                for k in ("colors", "detail", "palette", "seed", "max_side", "backing")
            },
        }
    )
    with _CACHE_LOCK:
        hit = key in _IMAGE_CACHE
        if hit:
            result = _IMAGE_CACHE.pop(key)
        else:
            result = abstract_image(source, config)
        _IMAGE_CACHE[key] = result
        while len(_IMAGE_CACHE) > 4:
            _IMAGE_CACHE.popitem(last=False)
    import copy

    return replace(
        result,
        metrics={**copy.deepcopy(result.metrics), "image_cache_hit": hit, "image_stage_key": key},
    )


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def png_bytes(array):
    stream = io.BytesIO()
    Image.fromarray(array).save(stream, format="PNG")
    return stream.getvalue()


@dataclass
class Design:
    config: DesignConfig
    profile: FabricationProfile
    abstraction: AbstractionResult
    layers: list[PaperLayer]
    geometries: list
    width_mm: float
    height_mm: float
    source_bytes: bytes
    revision_id: str = field(default_factory=lambda: uuid4().hex)
    parent_revision_id: str | None = None
    report: dict | None = None
    repairs: list[dict] = field(default_factory=list)
    depth_assignments: dict[int, int] | None = None

    legacy_requires_conversion: bool = False
    scene_plan: ScenePlan | None = None
    production_receipts: list[dict] = field(default_factory=list)
    light_plan: dict | None = None

    @property
    def frame(self):
        return build_frame(
            FrameSpec(
                outer_width_mm=self.width_mm,
                outer_height_mm=self.height_mm,
                border_width_mm=self.config.frame_mm,
            )
        )

    def fingerprint(self):
        return digest(
            {
                "config": self.config.model_dump(),
                "profile": self.profile.model_dump(),
                "dimensions": [self.width_mm, self.height_mm],
                "legacy_requires_conversion": self.legacy_requires_conversion,
                "depth_assignments": self.depth_assignments,
                "scene_plan": self.scene_plan.model_dump() if self.scene_plan else None,
                **(
                    {"production_receipts": self.production_receipts}
                    if self.production_receipts
                    else {}
                ),
                **({"light_plan": self.light_plan} if self.light_plan is not None else {}),
                "bridge_receipts": [
                    {
                        k: r.get(k)
                        for k in (
                            "layer_id",
                            "operation",
                            "centerline_wkt",
                            "current_footprint_wkt",
                            "enabled",
                            "adjusted_width_mm",
                        )
                    }
                    for r in self.repairs
                ],
                "layers": [
                    {
                        "id": l.id,
                        "color": l.color,
                        "attachment": l.attachment,
                        "spacing_mm": float(l.spacing_mm),
                        "region_ids": l.region_ids,
                        "depth_source": l.depth_source,
                        "geometry": mapping(g),
                    }
                    for l, g in zip(self.layers, self.geometries)
                ],
            }
        )

    def report_is_current(self):
        return (
            self.report is not None
            and self.report.get("revision_id") == self.revision_id
            and self.report.get("fingerprint") == self.fingerprint()
            and self.report.get("checker_version") == CHECKER_VERSION
        )

    def composite(self):
        layers = []
        side = self.config.max_side
        shape = (
            (max(1, round(side * self.height_mm / self.width_mm)), side)
            if self.width_mm >= self.height_mm
            else (side, max(1, round(side * self.width_mm / self.height_mm)))
        )
        for layer, geometry in zip(self.layers, self.geometries):
            mask = geometry_to_mask(geometry, shape, self.width_mm, self.height_mm)
            layers.append(PaperLayer(layer.id, layer.color, mask, layer.index, layer.spacing_mm))
        return composite_layers(layers)

    def material_accounting(self):
        wf = settings(self.config)
        return {
            "cut_sheet_count": len(self.layers),
            "colors_used": len({l.color for l in self.layers}),
            "uncut_backing_count": 0 if wf.lighting == "backlit" else 1,
            "diffuser_count": 1 if wf.lighting == "backlit" else 0,
            "colored_diffuser_count": 1 if self.light_plan else 0,
            "spacer_count": max(0, len(self.layers) - 1) if wf.assembly == "spaced" else 0,
            "status": "planned",
            "physical_test_status": "NOT_RUN",
        }

    def artwork_change_ratio(self, before):
        after = self.composite()
        changed = np.any(before != after, axis=2)
        window = geometry_to_mask(
            self.frame.artwork_window, changed.shape, self.width_mm, self.height_mm
        )
        return float(np.count_nonzero(changed & window) / max(1, np.count_nonzero(window)))

    def snapshot(self):
        return {
            "schema_version": 2,
            "revision_id": self.revision_id,
            "parent_revision_id": self.parent_revision_id,
            "fingerprint": self.fingerprint(),
            "config": self.config.model_dump(),
            "profile": self.profile.model_dump(),
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "source_base64": base64.b64encode(self.source_bytes).decode(),
            "source_sha256": hashlib.sha256(self.source_bytes).hexdigest(),
            "layers": [
                {
                    "id": l.id,
                    "color": l.color,
                    "index": i,
                    "spacing_mm": float(l.spacing_mm),
                    "region_ids": l.region_ids,
                    "attachment": l.attachment,
                    "depth_source": l.depth_source,
                    "geometry": mapping(g),
                }
                for i, (l, g) in enumerate(zip(self.layers, self.geometries))
            ],
            "report": self.report if self.report_is_current() else None,
            "repairs": self.repairs,
            "legacy_requires_conversion": self.legacy_requires_conversion,
            "depth_assignments": self.depth_assignments,
            "scene_plan": self.scene_plan.model_dump() if self.scene_plan else None,
            "production_receipts": self.production_receipts,
            "light_plan": self.light_plan,
        }


def validate_design(design, *, progress=None):
    reports = []
    for layer, geometry in zip(design.layers, design.geometries):
        if progress:
            progress(
                f"validate:{layer.id}", 0.65 + 0.25 * len(reports) / max(1, len(design.layers))
            )
        report = validate_geometry(
            geometry, design.profile, (design.width_mm, design.height_mm), frame=design.frame
        )
        from shapely import from_wkt

        for record in design.repairs:
            if (
                record["layer_id"] != layer.id
                or record["operation"] != "bridge"
                or not record.get("enabled", True)
            ):
                continue
            if "centerline_wkt" in record:
                corridor = from_wkt(record["centerline_wkt"]).buffer(
                    design.profile.min_bridge_width_mm / 2, cap_style="square"
                )
                if corridor.difference(geometry).area > 1e-6:
                    from .geometry import finding

                    report["findings"].append(
                        finding(
                            "THIN_BRIDGE",
                            corridor.difference(geometry),
                            "Bridge does not meet the current profile width",
                            required=design.profile.min_bridge_width_mm,
                        )
                    )
                    report["status"] = "FAIL"
        forbidden = geometry.intersection(
            protected_geometry(design.scene_plan, scene_index(design.scene_plan, layer.id))
        )
        if forbidden.area > 1e-9:
            from .geometry import finding

            report["findings"].append(
                finding(
                    "PROTECTED_CUTOUT_BLOCKED",
                    forbidden,
                    "Material blocks a protected cutout or light aperture",
                )
            )
            report["status"] = "FAIL"
        if design.scene_plan:
            index = scene_index(design.scene_plan, layer.id)
            from shapely.ops import unary_union

            cutouts = unary_union(
                [
                    shape(o.geometry)
                    for o in design.scene_plan.objects
                    if o.role == "cutout" and o.layer_index == index
                ]
            )
            protected = protected_geometry(design.scene_plan, index).union(cutouts)
            locked = (
                unary_union(
                    [
                        shape(o.geometry)
                        for o in design.scene_plan.objects
                        if o.role == "retain" and o.locked and o.layer_index == index
                    ]
                )
                .intersection(design.frame.artwork_window)
                .difference(protected)
            )
            missing_locked = locked.difference(geometry)
            if missing_locked.area > 1e-6:
                from .geometry import finding

                report["findings"].append(
                    finding(
                        "LOCKED_SUBJECT_REMOVED",
                        missing_locked,
                        "Retained material no longer covers locked scene objects",
                    )
                )
                report["status"] = "FAIL"
        report["layer_id"] = layer.id
        for issue in report["findings"]:
            issue["layer_id"] = layer.id
        reports.append(report)
    if design.legacy_requires_conversion:
        from .geometry import finding

        reports[0]["findings"].append(
            finding(
                "LEGACY_SCHEMA",
                design.geometries[0],
                "Convert this legacy draft explicitly before cut-ready export",
            )
        )
        reports[0]["status"] = "FAIL"
    try:
        check_scene_order(design.scene_plan, [l.id for l in design.layers])
    except (DesignError, ValueError, KeyError):
        from .geometry import finding

        reports[0]["findings"].append(
            finding(
                "OCCLUSION_ORDER_CONFLICT",
                design.geometries[0],
                "Current sheet order violates the authored occlusion plan",
            )
        )
        reports[0]["status"] = "FAIL"
    wf = settings(design.config)
    from .geometry import finding

    count_invalid = (wf.family == "stencil" and len(design.layers) != 1) or (
        wf.family == "depth_scene" and len(design.layers) < 2
    )
    if count_invalid and reports:
        reports[0]["findings"].append(
            finding(
                "INVALID_FAMILY_SHEET_COUNT",
                design.geometries[0],
                "Stencil needs exactly one cut sheet; Depth Scene needs at least two distinct cut sheets",
            )
        )
        reports[0]["status"] = "FAIL"
    light_path_ratio = None
    if wf.lighting == "backlit":
        from shapely.ops import unary_union

        open_path = design.frame.artwork_window.difference(unary_union(design.geometries))
        light_path_ratio = open_path.area / design.frame.artwork_window.area
        if (
            light_path_ratio < 1e-6
            and reports
            and not (design.light_plan and design.light_plan.get("mode") == "source_color_filter")
        ):
            reports[0]["findings"].append(
                finding(
                    "NO_OPEN_LIGHT_PATH",
                    design.frame.artwork_window,
                    "Opaque retained paper blocks every direct light path through the artwork window",
                )
            )
            reports[0]["status"] = "FAIL"
    statuses = [r["status"] for r in reports]
    status = (
        "FAIL"
        if not statuses or "FAIL" in statuses
        else ("WARNING" if "WARNING" in statuses else "PASS")
    )
    return {
        "status": status,
        "revision_id": design.revision_id,
        "fingerprint": design.fingerprint(),
        "checker_version": CHECKER_VERSION,
        "profile_hash": digest(design.profile.model_dump()),
        "layers": reports,
        "frame": design.frame.spec.model_dump(),
        "art_review_status": "pending_review",
        "light_path_ratio": light_path_ratio,
        "material_policy": "translucent paper + color-printed diffuser; transmission is an uncalibrated assumption"
        if design.light_plan
        else "opaque cut sheets; separate translucent diffuser for backlit assembly",
        "light_plan": design.light_plan,
        "physical_test_status": "NOT_RUN",
        "disclaimer": "PASS covers the declared continuous geometry checks; physical cutting has not been tested.",
    }


def fit_abstraction(source, config):
    from .imaging import normalize_image

    original = normalize_image(source, config.max_side)
    h, w = original.shape[:2]
    border = config.frame_mm
    if config.width_mm <= 2 * border:
        raise DesignError("Frame must leave a positive artwork window")
    height = config.height_mm or (config.width_mm - 2 * border) * h / w + 2 * border
    FrameSpec(outer_width_mm=config.width_mm, outer_height_mm=height, border_width_mm=border)
    if config.height_mm:
        ratio = (config.width_mm - 2 * border) / (height - 2 * border)
        if math.isclose(ratio, w / h, rel_tol=1e-9):
            return cached_abstraction(source, config), height
        size = (
            (config.max_side, max(1, round(config.max_side / ratio)))
            if ratio >= 1
            else (max(1, round(config.max_side * ratio)), config.max_side)
        )
        image = Image.fromarray(original)
        fitted = (
            ImageOps.fit(image, size)
            if config.artwork_fit == "cover"
            else ImageOps.pad(image, size, color=config.backing)
        )
        return cached_abstraction(png_bytes(np.asarray(fitted)), config), height
    return cached_abstraction(source, config), height


def generate_design(
    source: bytes,
    config=None,
    profile=None,
    depths=None,
    auto_repair=False,
    scene_plan=None,
    *,
    progress=None,
    production=False,
    source_lighting=True,
    paper_transmittance=0.55,
):
    progress = progress or (lambda stage, value: None)
    timings = {}
    started = time.perf_counter()
    config = config or DesignConfig()
    wf = settings(config)
    if wf.style == "splash_portrait" and scene_plan is None:
        raise DesignError(
            "Splash Portrait requires an authored style target ScenePlan; generative adapter unavailable",
            "CAPABILITY_UNAVAILABLE",
        )
    if wf.style == "portal" and scene_plan is None:
        raise DesignError(
            "Portal requires an authored ScenePlan with object roles and depths",
            "CAPABILITY_UNAVAILABLE",
        )
    profile = profile or FabricationProfile()
    if config.assembly_mode == "mounted_pieces":
        raise DesignError("Use frame_connected: mounted pieces are not supported cut sheets")
    config = config.model_copy(update={"assembly_mode": "frame_connected"})
    progress("image_abstraction", 0.05)
    phase_started = time.perf_counter()
    abstraction, height = fit_abstraction(source, config)
    timings["image_abstraction"] = time.perf_counter() - phase_started
    progress("construct_layers", 0.3)
    if wf.family == "stencil" and scene_plan is None:
        from .stencil import stencil_layer

        layers = stencil_layer(abstraction, config)
    else:
        layers = construct_layers(abstraction, config, depths=depths) if scene_plan is None else []
    frame = build_frame(
        FrameSpec(
            outer_width_mm=config.width_mm, outer_height_mm=height, border_width_mm=config.frame_mm
        )
    )
    phase_started = time.perf_counter()
    if scene_plan is not None:
        scene_plan = ScenePlan.model_validate(scene_plan)
        layers, geometries = compile_scene(scene_plan, frame, config, abstraction.regions.shape)
    else:
        geometries = []
        for index, layer in enumerate(layers):
            progress(f"vectorize:{layer.id}", 0.4 + 0.2 * index / max(1, len(layers)))
            geometries.append(material_from_mask(layer.mask, frame))
    timings["vectorize"] = time.perf_counter() - phase_started
    design = Design(
        config, profile, abstraction, layers, geometries, config.width_mm, height, source
    )
    design.scene_plan = scene_plan
    if production and wf.lighting == "backlit" and source_lighting:
        from .fabrication.light import lighting_plan

        design.light_plan = lighting_plan(design, transmittance=paper_transmittance)
    design.depth_assignments = (
        {int(k): v for k, v in depths.items()} if depths is not None else None
    )
    if config.mode == "multicolor":
        abstraction.metrics.update(layer_metrics(layers, abstraction.flat))
    phase_started = time.perf_counter()
    if production:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union

        from .fabrication.production import fabricate_sheet
        from .fabrication.scene import scene_index

        before_composite = design.composite()
        for index, (layer, geometry) in enumerate(zip(design.layers, design.geometries)):
            progress(f"fabricate:{layer.id}", 0.61 + 0.27 * index / len(design.layers))
            scene_layer = scene_index(design.scene_plan, layer.id)
            protected = protected_geometry(design.scene_plan, scene_layer)
            locked = (
                unary_union(
                    [
                        shape(o.geometry)
                        for o in design.scene_plan.objects
                        if o.locked and o.role == "retain" and o.layer_index == scene_layer
                    ]
                )
                .intersection(design.frame.artwork_window)
                .difference(protected)
                if design.scene_plan
                else Polygon()
            )
            material, receipt = fabricate_sheet(
                geometry,
                design.frame,
                design.profile,
                protected=protected,
                locked=locked,
                support_zones=unary_union(
                    [shape(g) for g in design.scene_plan.allowed_support_zones]
                )
                if design.scene_plan
                else None,
            )
            design.geometries[index] = material
            design.production_receipts.append({"layer_id": layer.id, **receipt})
        impact = design.artwork_change_ratio(before_composite)
        if impact > 0.05:
            raise DesignError(
                f"Fabrication would change {impact:.1%} of visible artwork; maximum is 5%",
                "INFEASIBLE_CONSTRAINTS",
            )
        design.abstraction.metrics["fabrication_visible_change_ratio"] = impact
        design.report = validate_design(design)
        if design.report["status"] != "PASS":
            codes = sorted({f["code"] for l in design.report["layers"] for f in l["findings"]})
            raise DesignError(
                "Production candidate failed final stack checks: " + ", ".join(codes),
                "INFEASIBLE_CONSTRAINTS",
            )
    else:
        design.report = validate_design(design, progress=progress)
    timings["validate"] = time.perf_counter() - phase_started
    if auto_repair and not production:
        progress("repair", 0.92)
        phase_started = time.perf_counter()
        design = edit_design(design, "repair", {})
        timings["repair"] = time.perf_counter() - phase_started
    progress("save", 0.97)
    timings["total"] = time.perf_counter() - started
    design.abstraction.metrics["stage_timings_seconds"] = timings
    return design


def restore_design(snapshot):
    try:
        if snapshot.get("schema_version") not in (1, 2):
            raise DesignError("Unsupported project schema_version")
        source = base64.b64decode(snapshot["source_base64"], validate=True)
        if (
            len(source) > 30_000_000
            or hashlib.sha256(source).hexdigest() != snapshot["source_sha256"]
        ):
            raise DesignError("Project source size/hash is invalid")
        config = DesignConfig(**snapshot["config"])
        profile = FabricationProfile(**snapshot["profile"])
        dimensions = (snapshot["width_mm"], snapshot["height_mm"])
        if any(
            not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 < v <= 2000
            for v in dimensions
        ):
            raise DesignError("Project physical dimensions are invalid")
        if not 1 <= len(snapshot["layers"]) <= 24:
            raise DesignError("Project must contain between 1 and 24 layers")
        abstraction, _ = fit_abstraction(source, config)
        scene_plan = (
            ScenePlan.model_validate(snapshot["scene_plan"]) if snapshot.get("scene_plan") else None
        )
        light_plan = None
        if snapshot.get("light_plan") is not None:
            from .fabrication.light import SourceLightPlan

            light_plan = SourceLightPlan.model_validate(snapshot["light_plan"]).model_dump()
            if (
                light_plan["source_grid_sha256"] != abstraction.source_hash
                or settings(config).lighting != "backlit"
            ):
                raise DesignError(
                    "Color diffuser plan does not match the current source or lighting mode"
                )
        raw_depths = snapshot.get("depth_assignments")
        manual_depths = (
            {int(k): v for k, v in raw_depths.items()} if raw_depths is not None else None
        )
        if manual_depths is not None:
            construct_layers(abstraction, config, depths=manual_depths)
        geometries, layers = [], []
        layer_ids = set()
        for index, item in enumerate(snapshot["layers"]):
            item = SnapshotLayer(**item).model_dump()
            if (config.assembly_mode == "full_sheet" and item["attachment"] is not None) or (
                config.assembly_mode == "mounted_pieces" and not item["attachment"]
            ):
                raise DesignError("Attachment metadata conflicts with assembly mode")
            if not re.fullmatch(r"layer-[0-9]{2}", item["id"]) or item["id"] in layer_ids:
                raise DesignError("Project layer ids are invalid or duplicated")
            layer_ids.add(item["id"])
            DesignConfig.color(item["color"])
            if item["index"] != index or not 0 <= item["spacing_mm"] <= 100:
                raise DesignError("Project layer order/spacing is invalid")
            geometry = shape(item["geometry"])
            if not geometry.is_valid or geometry.is_empty:
                raise DesignError("Project contains invalid geometry")
            geometries.append(geometry)
            mask = geometry_to_mask(
                geometry, abstraction.regions.shape, snapshot["width_mm"], snapshot["height_mm"]
            )
            layers.append(
                PaperLayer(
                    item["id"],
                    item["color"],
                    mask,
                    item["index"],
                    item["spacing_mm"],
                    item["region_ids"],
                    item["attachment"],
                    item["depth_source"],
                )
            )
        assigned_regions = [region for layer in layers for region in layer.region_ids]
        if scene_plan is None and sorted(assigned_regions) != list(
            range(len(abstraction.region_info))
        ):
            raise DesignError("Project layer regions must partition the source regions")
        repairs = snapshot.get("repairs", [])
        if not isinstance(repairs, list) or len(repairs) > 1024:
            raise DesignError("Invalid repair history")
        from shapely import from_wkt

        for record in repairs:
            if (
                not isinstance(record, dict)
                or record.get("layer_id") not in layer_ids
                or record.get("operation")
                not in (
                    "bridge",
                    "topology_repair",
                    "remove_tiny_holes",
                    "thicken_material",
                    "simplify_cut_paths",
                )
            ):
                raise DesignError("Invalid repair metadata")
            for key in ("before_wkt", "after_wkt"):
                if key not in record or not from_wkt(record[key]).is_valid:
                    raise DesignError("Invalid repair geometry")
            if (
                "current_footprint_wkt" in record
                and not from_wkt(record["current_footprint_wkt"]).is_valid
            ):
                raise DesignError("Invalid bridge footprint")
            if "centerline_wkt" in record:
                centerline = from_wkt(record["centerline_wkt"])
                if (
                    not centerline.is_valid
                    or centerline.is_empty
                    or centerline.geom_type != "LineString"
                    or not all(math.isfinite(v) for v in centerline.bounds)
                ):
                    raise DesignError("Bridge centerline must be a finite nonempty LineString")
        design = Design(
            config,
            profile,
            abstraction,
            layers,
            geometries,
            snapshot["width_mm"],
            snapshot["height_mm"],
            source,
            snapshot["revision_id"],
            snapshot.get("parent_revision_id"),
            snapshot.get("report"),
            repairs,
            manual_depths,
            snapshot.get("schema_version") == 1
            or snapshot.get("legacy_requires_conversion", False),
            scene_plan,
            snapshot.get("production_receipts", []),
            light_plan,
        )
        if snapshot["schema_version"] == 2 and design.fingerprint() != snapshot["fingerprint"]:
            raise DesignError("Project geometry/config fingerprint does not match")
        # Imported reports are untrusted evidence; re-run local checks for the imported geometry.
        design.report = validate_design(design)
        return design
    except (KeyError, TypeError, ValueError, GEOSException) as exc:
        if isinstance(exc, DesignError):
            raise
        raise DesignError("Malformed project snapshot") from exc


def edit_design(design, operation, payload):
    import copy

    updated = copy.deepcopy(design)
    updated.parent_revision_id = design.revision_id
    updated.revision_id = uuid4().hex
    updated.report = None
    if operation == "convert_legacy":
        if not updated.legacy_requires_conversion:
            raise DesignError("Project already uses framed fabrication")
        from shapely.affinity import translate

        b = updated.config.frame_mm
        updated.geometries = [
            updated.frame.required_frame.union(
                translate(
                    scale(
                        g,
                        (updated.width_mm - 2 * b) / updated.width_mm,
                        (updated.height_mm - 2 * b) / updated.height_mm,
                        origin=(0, 0),
                    ),
                    b,
                    b,
                )
            )
            for g in updated.geometries
        ]
        updated.legacy_requires_conversion = False
        updated.config = updated.config.model_copy(update={"assembly_mode": "frame_connected"})
        for layer in updated.layers:
            layer.attachment = None
        updated.repairs = []
        updated.report = validate_design(updated)
    elif operation == "set_color":
        color = DesignConfig.color(payload["color"])
        layer = next((l for l in updated.layers if l.id == payload["layer_id"]), None)
        if layer is None:
            raise DesignError("Unknown layer")
        layer.color = color
    elif operation == "reorder":
        ids = payload["layer_ids"]
        if len(ids) != len(updated.layers) or set(ids) != {l.id for l in updated.layers}:
            raise DesignError("Layer order must contain every layer exactly once")
        check_scene_order(updated.scene_plan, ids)
        by_id = {l.id: (l, g) for l, g in zip(updated.layers, updated.geometries)}
        updated.layers = [by_id[key][0] for key in ids]
        updated.geometries = [by_id[key][1] for key in ids]
        for index, layer in enumerate(updated.layers):
            layer.index = index
    elif operation == "set_spacing":
        config = DesignConfig(
            **{**updated.config.model_dump(), "spacing_mm": payload["spacing_mm"]}
        )
        updated.config = config
        for layer in updated.layers:
            layer.spacing_mm = config.spacing_mm
    elif operation == "set_profile":
        updated.profile = FabricationProfile(**{**updated.profile.model_dump(), **payload})
    elif operation == "set_dimensions":
        new_width = DesignConfig(
            **{**updated.config.model_dump(), "width_mm": payload["width_mm"]}
        ).width_mm
        factor = new_width / updated.width_mm
        updated.geometries = [scale(g, factor, factor, origin=(0, 0)) for g in updated.geometries]
        if updated.scene_plan:
            plan = updated.scene_plan.model_dump()
            for obj in plan["objects"]:
                obj["geometry"] = mapping(
                    scale(shape(obj["geometry"]), factor, factor, origin=(0, 0))
                )
            for key in ("protected_cutouts", "protected_light_apertures", "allowed_support_zones"):
                plan[key] = [
                    mapping(scale(shape(g), factor, factor, origin=(0, 0))) for g in plan[key]
                ]
            updated.scene_plan = ScenePlan.model_validate(plan)
        from shapely import from_wkt

        for record in updated.repairs:
            for key in ("before_wkt", "after_wkt", "current_footprint_wkt", "centerline_wkt"):
                if key in record:
                    record[key] = scale(from_wkt(record[key]), factor, factor, origin=(0, 0)).wkt
            for key in ("bridge_width_mm", "adjusted_width_mm"):
                if key in record:
                    record[key] *= factor
        updated.width_mm, updated.height_mm = new_width, updated.height_mm * factor
        updated.config = DesignConfig(
            **{
                **updated.config.model_dump(),
                "width_mm": new_width,
                "height_mm": updated.height_mm,
                "frame_mm": updated.config.frame_mm * factor,
            }
        )
    elif operation == "set_bridge":
        from shapely import from_wkt
        from shapely.geometry import box

        index = payload.get("repair_index")
        if type(index) is not int or not 0 <= index < len(updated.repairs):
            raise DesignError("Unknown repair record")
        record = updated.repairs[index]
        if record["operation"] != "bridge" or type(payload.get("enabled")) is not bool:
            raise DesignError("Select a bridge record and an enabled state")
        layer_index = next(
            i for i, layer in enumerate(updated.layers) if layer.id == record["layer_id"]
        )
        before = from_wkt(record["before_wkt"])
        base_added = from_wkt(record["after_wkt"]).difference(before)
        applied = from_wkt(record.get("current_footprint_wkt", base_added.wkt))
        original_composite = updated.composite()
        geometry = updated.geometries[layer_index]
        enabled = payload["enabled"]
        existing = applied if record.get("enabled", True) else applied.difference(applied)
        new_footprint = applied
        if "width_mm" in payload:
            width = float(payload["width_mm"])
            if not math.isfinite(width) or not updated.profile.min_bridge_width_mm <= width <= 20:
                raise DesignError("Bridge width must meet the profile and be at most 20mm")
            base_width = record.get(
                "bridge_width_mm",
                max(
                    updated.profile.min_bridge_width_mm, updated.profile.min_feature_width_mm * 1.5
                ),
            )
            new_footprint = (
                from_wkt(record["centerline_wkt"])
                .buffer(width / 2, cap_style="square")
                .difference(before)
                if "centerline_wkt" in record
                else base_added.buffer((width - base_width) / 2).difference(before)
            )
            record["adjusted_width_mm"] = width
        sheet = box(0, 0, updated.width_mm, updated.height_mm)
        new_footprint = new_footprint.intersection(sheet)
        updated.geometries[layer_index] = geometry.difference(existing)
        if enabled:
            updated.geometries[layer_index] = updated.geometries[layer_index].union(new_footprint)
        record["current_footprint_wkt"] = new_footprint.wkt
        impact = updated.artwork_change_ratio(original_composite)
        if impact > 0.05:
            raise DesignError(
                "Bridge edit exceeds the 5% visual change budget", "INFEASIBLE_CONSTRAINTS"
            )
        record["enabled"] = enabled
        updated.report = validate_design(updated)
    elif operation == "repair":
        original_composite = updated.composite()
        from shapely.ops import unary_union

        support_zones = (
            unary_union([shape(g) for g in updated.scene_plan.allowed_support_zones])
            if updated.scene_plan
            else None
        )
        candidate_geometries, repairs = [], []
        for layer, geometry in zip(updated.layers, updated.geometries):
            repaired, records = repair_geometry(
                geometry,
                updated.profile,
                (updated.width_mm, updated.height_mm),
                attachment=layer.attachment,
                frame=updated.frame,
                support_zones=support_zones,
                protected=protected_geometry(
                    updated.scene_plan, scene_index(updated.scene_plan, layer.id)
                ),
            )
            candidate_geometries.append(repaired)
            repairs.extend(
                {
                    **record,
                    "layer_id": layer.id,
                    "input_revision_id": design.revision_id,
                    "output_revision_id": updated.revision_id,
                }
                for record in records
            )
        previous = updated.geometries
        updated.geometries = candidate_geometries
        impact = updated.artwork_change_ratio(original_composite)
        if impact > 0.05:
            updated.geometries = previous
            raise DesignError(
                "Repair exceeds the 5% composite visual change budget", "INFEASIBLE_CONSTRAINTS"
            )
        updated.repairs += [{**record, "composite_change_ratio": impact} for record in repairs]
        updated.report = validate_design(updated)
    else:
        raise DesignError("Unsupported edit operation")
    return updated
