"""Batch image abstraction, reproducible benchmarks and the local studio launcher."""

import argparse
import hashlib
import json
import platform
import sys
import threading
import time
from pathlib import Path

import numpy as np
import psutil

from .export import export_bundle
from .imaging import abstract_image, region_overlay
from .models import DesignConfig, DesignError, hex_color
from .pipeline import generate_design, png_bytes


def write_abstraction(source, destination, config):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    result = abstract_image(source, config)
    for name, array in [
        ("original", result.original),
        ("flat", result.flat),
        ("regions", region_overlay(result)),
    ]:
        (destination / f"{name}.png").write_bytes(png_bytes(array))
    np.save(destination / "regions.npy", result.regions, allow_pickle=False)
    np.save(destination / "color-labels.npy", result.color_labels, allow_pickle=False)
    np.save(destination / "superpixels.npy", result.superpixels, allow_pickle=False)
    region_directory = destination / "region-masks"
    region_directory.mkdir(exist_ok=True)
    packed = {
        f"region_{region['id']}": np.packbits(result.regions == region["id"])
        for region in result.region_info
    }
    np.savez_compressed(destination / "region-masks.npz", **packed)
    # Keep a small browsable sample; archive every mask without thousands of filesystem writes.
    for region in result.region_info[:64]:
        mask = (result.regions == region["id"]).astype(np.uint8) * 255
        (region_directory / f"region-{region['id']:05}.png").write_bytes(png_bytes(mask))
    (destination / "palette.json").write_text(
        json.dumps([hex_color(c) for c in result.palette]), encoding="utf-8"
    )
    (destination / "regions.json").write_text(
        json.dumps({"regions": result.region_info, "adjacency": result.adjacency}), encoding="utf-8"
    )
    (destination / "metrics.json").write_text(
        json.dumps(result.metrics, indent=2), encoding="utf-8"
    )
    files = {
        str(path.relative_to(destination)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in destination.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "schema_version": 1,
        "source_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
        "normalized_sha256": result.source_hash,
        "config": config.model_dump(),
        "region_masks": {
            "file": "region-masks.npz",
            "encoding": "numpy.packbits, big endian",
            "shape": list(result.regions.shape),
            "count": len(result.region_info),
            "png_preview_limit": 64,
        },
        "files": files,
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return result


def benchmark(manifest_path, destination, config):
    manifest_path, destination = Path(manifest_path), Path(destination)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=True)
    runs = []
    for index, item in enumerate(manifest["images"]):
        path = Path(item["path"])
        if not path.is_absolute():
            path = manifest_path.parent / path
        done = threading.Event()
        process = psutil.Process()
        peak = [process.memory_info().rss]

        def sample(done=done, peak=peak, process=process):
            while not done.wait(0.01):
                peak[0] = max(peak[0], process.memory_info().rss)

        monitor = threading.Thread(target=sample, daemon=True)
        monitor.start()
        start = time.perf_counter()
        run = {
            "input": str(path),
            "license": item.get("license", "UNSPECIFIED"),
            "seed": config.seed,
        }
        try:
            result = write_abstraction(path, destination / f"{index:03}", config)
            run.update(
                status="succeeded",
                metrics=result.metrics,
                source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        except Exception as exc:  # noqa: BLE001 -- a failed sample must remain in the benchmark report.
            run.update(status="failed", error=str(exc))
        finally:
            peak[0] = max(peak[0], process.memory_info().rss)
            done.set()
            monitor.join()
        run.update(elapsed_seconds=time.perf_counter() - start, peak_rss_bytes=peak[0])
        runs.append(run)
    latency = [r["elapsed_seconds"] for r in runs if r["status"] == "succeeded"]
    report = {
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "hardware": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu": platform.processor(),
            "logical_cpus": psutil.cpu_count(),
        },
        "config": config.model_dump(),
        "runs": runs,
        "median_seconds": float(np.median(latency)) if latency else None,
        "p95_seconds": float(np.percentile(latency, 95)) if latency else None,
        "note": "RSS sampled every 10ms; includes interpreter and dependencies. Not a physical fabrication test.",
    }
    (destination / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="PaperCraft AI — local CPU papercut studio")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("abstract", "generate", "benchmark"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("input", type=Path)
        subparser.add_argument("--output", "-o", type=Path, required=True)
        subparser.add_argument("--colors", type=int, default=6)
        subparser.add_argument("--layers", type=int, default=8)
        subparser.add_argument("--detail", type=float, default=0.6)
        subparser.add_argument("--width-mm", type=float, default=200)
        subparser.add_argument("--max-side", type=int, default=384)
        subparser.add_argument("--seed", type=int, default=42)
        subparser.add_argument("--palette", help="Comma-separated #RRGGBB colors")
        subparser.add_argument("--mode", choices=["multicolor", "lightbox"], default="multicolor")
    serve = subparsers.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--storage", default=".papercraft")
    reference = subparsers.add_parser("reference")
    reference.add_argument("--mode", choices=["multicolor", "lightbox"], default="multicolor")
    reference.add_argument("--output", "-o", type=Path, required=True)
    reference.add_argument("--paper", choices=["A4", "A3"], default="A4")
    reference.add_argument(
        "--orientation", choices=["auto", "portrait", "landscape"], default="auto"
    )
    reference.add_argument("--print-side", choices=["front", "back"], default="front")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            import uvicorn

            from .api import create_app

            uvicorn.run(create_app(args.storage), host="127.0.0.1", port=args.port)
            return
        if args.command == "reference":
            import io
            import zipfile

            from .fabrication.models import PrintLayout
            from .fabrication.reference import forest_reference

            design = forest_reference(args.mode)
            data = export_bundle(
                design,
                "cut_ready",
                PrintLayout(
                    paper=args.paper, orientation=args.orientation, print_side=args.print_side
                ),
            )
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "forest-deer.zip").write_bytes(data)
            # These names come only from the trusted local exporter, not an imported archive.
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                archive.extractall(args.output)
            print(
                json.dumps(
                    {
                        "status": design.report["status"],
                        "cut_sheet_count": len(design.layers),
                        "output": str(args.output),
                    }
                )
            )
            return
        config = DesignConfig(
            colors=args.colors,
            layers=args.layers,
            detail=args.detail,
            width_mm=args.width_mm,
            max_side=args.max_side,
            seed=args.seed,
            palette=args.palette.split(",") if args.palette else None,
            mode=args.mode,
        )
        if args.command == "abstract":
            result = write_abstraction(args.input, args.output, config)
            print(json.dumps(result.metrics))
        elif args.command == "benchmark":
            report = benchmark(args.input, args.output, config)
            print(
                json.dumps(
                    {"runs": len(report["runs"]), "median_seconds": report["median_seconds"]}
                )
            )
            if any(r["status"] == "failed" for r in report["runs"]):
                sys.exit(1)
        else:
            design = generate_design(args.input.read_bytes(), config)
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "papercraft-draft.zip").write_bytes(export_bundle(design))
            print(
                json.dumps({"revision_id": design.revision_id, "status": design.report["status"]})
            )
    except (DesignError, OSError, ValueError) as exc:
        print(
            json.dumps({"error": getattr(exc, "code", "INVALID_INPUT"), "message": str(exc)}),
            file=sys.stderr,
        )
        sys.exit(1)
