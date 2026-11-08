"""Isolated, inspectable CPU/model experiments; never replace the source project."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import psutil
from PIL import Image, ImageDraw
from shapely.affinity import translate

from .geometry import geometry_to_mask
from .imaging import visual_metrics
from .layers import PaperLayer, composite_layers
from .models import AISettings, DesignConfig, DesignError, FabricationProfile, rgb
from .pipeline import fit_abstraction, generate_design, png_bytes
from .workflows import settings

_COMPARISON_LOCK = threading.Lock()


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, allow_nan=False, indent=2), encoding="utf8")
    temporary.replace(path)


def render_artwork(design):
    border = design.config.frame_mm
    shape = design.abstraction.regions.shape
    layers = [
        PaperLayer(
            layer.id,
            layer.color,
            geometry_to_mask(
                translate(geometry, -border, -border),
                shape,
                design.width_mm - 2 * border,
                design.height_mm - 2 * border,
            ),
            index,
            layer.spacing_mm,
        )
        for index, (layer, geometry) in enumerate(zip(design.layers, design.geometries))
    ]
    rgba = composite_layers(layers)
    background = np.empty((*shape, 3), np.uint8)
    background[:] = rgb(design.config.backing)
    return np.where(rgba[:, :, 3:4] > 0, rgba[:, :, :3], background).astype(np.uint8)


def render_sheets(design, output):
    columns = min(4, len(design.layers))
    rows = (len(design.layers) + columns - 1) // columns
    board = Image.new("RGB", (columns * 260, rows * 286), "#eeeeee")
    draw = ImageDraw.Draw(board)
    height = max(1, round(240 * design.height_mm / design.width_mm))
    size = (
        (min(240, round(240 * design.width_mm / design.height_mm)), 240)
        if height > 240
        else (240, height)
    )
    for index, (layer, geometry) in enumerate(zip(design.layers, design.geometries)):
        mask = geometry_to_mask(geometry, (size[1], size[0]), design.width_mm, design.height_mm)
        paper = np.full((*mask.shape, 3), 255, np.uint8)
        y, x = np.indices(mask.shape)
        paper[(~mask) & ((x + y) % 10 < 1)] = [190, 190, 190]
        paper[mask] = rgb(layer.color)
        tile = Image.fromarray(paper)
        tile.save(output / f"sheet-{index + 1:02}.png")
        ox, oy = (index % columns) * 260 + 10, (index // columns) * 286 + 10
        board.paste(tile, (ox, oy))
        draw.text((ox, oy + 246), layer.id, fill="black")
    board.save(output / "sheets.png")


def evaluate_variant(
    source, config, profile, output, variant, *, progress=None, generation_options=None
):
    output.mkdir(parents=True, exist_ok=True)
    flags = config.ai or AISettings(
        segmentation=True, face_features=True, depth=settings(config).family == "depth_scene"
    )
    flags = flags.model_copy(update={"failure_policy": "strict"})
    if not any((flags.segmentation, flags.depth, flags.face_features)):
        flags = flags.model_copy(update={"segmentation": True})
    actual = config.model_copy(update={"ai": None if variant == "cpu" else flags})
    started = time.perf_counter()
    result = {
        "variant": variant,
        "config": actual.model_dump(),
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "cut_ready": False,
        "status": "failed",
        "identity_review": "NOT_RUN",
        "physical_test_status": "NOT_RUN",
    }
    try:
        abstraction, _ = fit_abstraction(source, actual)
        (output / "source.png").write_bytes(png_bytes(abstraction.original))
        (output / "proposal.png").write_bytes(png_bytes(abstraction.flat))
        result["diagnostics"] = abstraction.metrics.get("ai_diagnostics", [])
        if variant == "model" and not any(d["status"] == "applied" for d in result["diagnostics"]):
            raise DesignError("No requested model component was applied", "CAPABILITY_UNAVAILABLE")
        design = generate_design(
            source,
            actual,
            profile,
            production=True,
            progress=progress,
            **(generation_options or {}),
        )
        final = render_artwork(design)
        (output / "cut.png").write_bytes(png_bytes(final))
        render_sheets(design, output)
        write_json(output / "project.json", design.snapshot())
        from .export import export_bundle

        (output / "cut-ready.zip").write_bytes(export_bundle(design, "cut_ready"))
        quality = visual_metrics(abstraction.original, final)
        target = np.any(final != abstraction.flat, axis=2)
        quality["target_changed_pixel_ratio"] = float(target.mean())
        result.update(
            status=design.report["status"],
            cut_ready=design.report["status"] == "PASS",
            quality=quality,
            fingerprint=design.fingerprint(),
            layer_count=len(design.layers),
            bridge_count=sum(r.get("bridge_count", 0) for r in design.production_receipts),
            construction_plan=design.abstraction.metrics.get("construction_plan"),
            material_changes=[
                {
                    "layer_id": r["layer_id"],
                    "total": r["material_change_ratio"],
                    "exposed": r.get("exposed_change_ratio", r["material_change_ratio"]),
                }
                for r in design.production_receipts
            ],
            fabrication=design.report,
            cut_length_mm=sum(g.length for g in design.geometries),
        )
    except Exception as exc:
        if getattr(exc, "code", None) in ("JOB_CANCELLED", "STALE_REVISION"):
            raise
        result["error"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
    result["elapsed_seconds"] = time.perf_counter() - started
    result["artifacts"] = [p.name for p in output.iterdir() if p.suffix == ".png"]
    write_json(output / "result.json", result)
    return result


def assess(variants):
    valid = [name for name, item in variants.items() if item.get("cut_ready")]
    assessment = {
        "recommended": "none",
        "identity_review": "NOT_RUN",
        "basis": "geometry_then_color_and_boundary; no physical or identity certificate",
    }
    if len(valid) == 1:
        assessment.update(
            recommended=valid[0], reason="Only this candidate passed fabrication checks"
        )
    elif len(valid) == 0:
        assessment["reason"] = "Neither candidate produced a certified design"
    else:
        cpu, model = (variants[name]["quality"] for name in ("cpu", "model"))
        model_color = model["delta_e_mean"] < cpu["delta_e_mean"] * 0.95
        cpu_color = cpu["delta_e_mean"] < model["delta_e_mean"] * 0.95
        boundary_delta = model["boundary_f_score"] - cpu["boundary_f_score"]
        if model_color and boundary_delta >= -0.02:
            assessment.update(
                recommended="model", reason="Lower color error without material boundary regression"
            )
        elif cpu_color and boundary_delta <= 0.02:
            assessment.update(
                recommended="cpu", reason="Lower color error without material boundary regression"
            )
        else:
            assessment.update(
                recommended="tie",
                reason="No clear color/boundary winner; inspect recognizability and cutting complexity",
            )
    return assessment


def terminate_worker(process):
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    for child in children:
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    try:
        process.kill()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise DesignError(
            "Comparison worker did not terminate within 5 seconds", "RESOURCE_LIMIT"
        ) from exc


def compare(
    source, config, profile, output, *, progress=None, timeout=180, generation_options=None
):
    if not _COMPARISON_LOCK.acquire(blocking=False):
        raise DesignError(
            "Another comparison is already running; wait for it to finish", "RESOURCE_LIMIT"
        )
    try:
        return _compare(
            source,
            config,
            profile,
            output,
            progress=progress,
            timeout=timeout,
            generation_options=generation_options,
        )
    finally:
        _COMPARISON_LOCK.release()


def _compare(
    source, config, profile, output, *, progress=None, timeout=180, generation_options=None
):
    output.mkdir(parents=True, exist_ok=True)
    (output / "input.bin").write_bytes(source)
    generation_options = {**(generation_options or {}), "time_limit_seconds": timeout}
    write_json(
        output / "request.json",
        {
            "config": config.model_dump(),
            "profile": profile.model_dump(),
            "generation_options": generation_options,
        },
    )
    results = {}
    for index, variant in enumerate(("cpu", "model")):
        folder = output / variant
        folder.mkdir(exist_ok=True)
        started = time.perf_counter()
        peak = 0
        with (folder / "worker.log").open("w", encoding="utf8") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "papercraft_ai.comparison",
                    "--worker",
                    str(output.resolve()),
                    variant,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                while process.poll() is None:
                    if progress:
                        stage = "starting"
                        try:
                            status = json.loads((folder / "progress.json").read_text())
                            stage = status["stage"]
                        except (OSError, ValueError):
                            pass
                        progress(f"compare_{variant}:{stage}", index * 0.5 + 0.1)
                    if time.perf_counter() - started > timeout:
                        raise DesignError(
                            f"{variant} comparison exceeded {timeout}s", "RESOURCE_LIMIT"
                        )
                    try:
                        root = psutil.Process(process.pid)
                        peak = max(
                            peak,
                            sum(
                                p.memory_info().rss for p in [root, *root.children(recursive=True)]
                            ),
                        )
                    except psutil.NoSuchProcess:
                        pass
                    time.sleep(0.15)
            except Exception as exc:
                terminate_worker(process)
                if getattr(exc, "code", None) != "RESOURCE_LIMIT":
                    raise
                write_json(
                    folder / "result.json",
                    {
                        "status": "failed",
                        "cut_ready": False,
                        "error": {"code": "RESOURCE_LIMIT", "message": str(exc)},
                        "artifacts": [],
                    },
                )
        try:
            result = json.loads((folder / "result.json").read_text(encoding="utf8"))
        except (OSError, ValueError):
            result = {
                "status": "failed",
                "cut_ready": False,
                "artifacts": [],
                "error": {
                    "code": "WORKER_FAILED",
                    "message": "Worker exited before producing a result",
                },
            }
        result.update(peak_process_tree_rss_bytes=peak, wall_seconds=time.perf_counter() - started)
        results[variant] = result
    report = {
        "variants": results,
        "assessment": assess(results),
        "hardware": {"platform": platform.platform(), "python": platform.python_version()},
        "config": config.model_dump(),
        "profile": profile.model_dump(),
        "generation_options": generation_options or {},
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }
    write_json(output / "results.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", nargs=2, metavar=("DIRECTORY", "VARIANT"), required=True)
    arguments = parser.parse_args()
    directory, name = Path(arguments.worker[0]), arguments.worker[1]
    request = json.loads((directory / "request.json").read_text(encoding="utf8"))
    if name not in ("cpu", "model"):
        parser.error("Invalid variant")
    evaluate_variant(
        (directory / "input.bin").read_bytes(),
        DesignConfig.model_validate(request["config"]),
        FabricationProfile.model_validate(request["profile"]),
        directory / name,
        name,
        progress=lambda stage, fraction: write_json(
            directory / name / "progress.json", {"stage": stage, "fraction": fraction}
        ),
        generation_options=request.get("generation_options"),
    )
