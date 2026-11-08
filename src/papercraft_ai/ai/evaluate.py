"""Run comparable A/B/C/D (+ portrait E) in fresh subprocesses; no quality claims from smoke runs."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil


def worker(image, output, variant, config):
    from ..models import AISettings, DesignConfig
    from ..pipeline import generate_design, png_bytes

    flags = {
        "A": {},
        "B": {"segmentation": True},
        "C": {"depth": True},
        "D": {"segmentation": True, "depth": True},
        "E": {"segmentation": True, "face_features": True},
    }
    base = DesignConfig.model_validate(config)
    base = base.model_copy(
        update={
            "ai": AISettings(**flags[variant], failure_policy="strict") if flags[variant] else None
        }
    )
    output.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    report = {
        "variant": variant,
        "config": base.model_dump(),
        "source_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "quality_acceptance": "NOT_RUN",
        "physical_test": "NOT_RUN",
    }
    try:
        # Save the real proposal even when the manufacturing constraints prove infeasible.
        draft = generate_design(image.read_bytes(), base)
        (output / "proposal.png").write_bytes(png_bytes(draft.composite()))
        (output / "analysis.json").write_text(
            json.dumps(draft.abstraction.metrics, indent=2), encoding="utf8"
        )
        design = generate_design(image.read_bytes(), base, production=True)
        (output / "cut-composite.png").write_bytes(png_bytes(design.composite()))
        snapshot = design.snapshot()
        (output / "project.json").write_text(json.dumps(snapshot), encoding="utf8")
        report.update(
            status="succeeded",
            fabrication=design.report["status"],
            fingerprint=design.fingerprint(),
            metrics=design.abstraction.metrics,
            bridge_count=sum(r.get("bridge_count", 0) for r in design.production_receipts),
            cut_length_mm=sum(g.length for g in design.geometries),
        )
    except Exception as exc:  # noqa: BLE001 -- record failed candidates; never discard them.
        report.update(
            status="failed", code=getattr(exc, "code", type(exc).__name__), error=str(exc)
        )
    report["cold_total_seconds"] = time.perf_counter() - start
    (output / "run.json").write_text(json.dumps(report, indent=2), encoding="utf8")


def run(manifest_path, output, timeout=300, variants="ABCDE"):
    data = json.loads(manifest_path.read_text(encoding="utf8"))
    output.mkdir(parents=True, exist_ok=True)
    runs = []
    for index, item in enumerate(data["images"]):
        image = (manifest_path.parent / item["path"]).resolve()
        config = {**data.get("config", {}), **item.get("config", {})}
        for variant in variants:
            destination = output / f"{index:03}-{variant}"
            destination.mkdir(parents=True, exist_ok=True)
            with (destination / "process.log").open("w", encoding="utf8") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "papercraft_ai.ai.evaluate",
                        "--worker",
                        str(image),
                        str(destination.resolve()),
                        variant,
                        json.dumps(config),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                done = threading.Event()
                peak = [0]

                def sample(done=done, peak=peak, process=process):
                    while not done.wait(0.02):
                        try:
                            root_process = psutil.Process(process.pid)
                            total = sum(
                                p.memory_info().rss
                                for p in [root_process, *root_process.children(recursive=True)]
                                if p.is_running()
                            )
                            peak[0] = max(peak[0], total)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            return

                monitor = threading.Thread(target=sample, daemon=True)
                monitor.start()
                timed_out = False
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    for child in psutil.Process(process.pid).children(recursive=True):
                        try:
                            child.kill()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    process.kill()
                    process.wait()
                finally:
                    done.set()
                    monitor.join()
            result_path = destination / "run.json"
            result = (
                json.loads(result_path.read_text(encoding="utf8"))
                if result_path.exists()
                else {
                    "variant": variant,
                    "status": "failed",
                    "error": "timeout" if timed_out else "worker exited",
                }
            )
            result.update(
                input_index=index,
                peak_process_tree_rss_bytes=peak[0],
                annotations=item.get("annotations"),
                recognition_review="NOT_RUN",
            )
            runs.append(result)
            print(f"{index:03}-{variant}: {result['status']}", flush=True)
    report = {
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "hardware": {
            "platform": platform.platform(),
            "cpu": platform.processor(),
            "python": platform.python_version(),
            "logical_cpus": psutil.cpu_count(),
        },
        "runs": runs,
        "quality_acceptance": "NOT_RUN",
        "physical_test": "NOT_RUN",
        "notes": "Fresh process per variant; draft+production share warm perception. RSS sampled at 20ms; not VRAM. No recognition claims without annotation review.",
    }
    (output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", nargs=4, metavar=("IMAGE", "OUTPUT", "VARIANT", "CONFIG"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--variants", nargs="+", choices=list("ABCDE"), default=list("ABCDE"))
    args = parser.parse_args()
    if args.worker:
        worker(
            Path(args.worker[0]), Path(args.worker[1]), args.worker[2], json.loads(args.worker[3])
        )
    elif args.manifest and args.output:
        run(args.manifest, args.output, args.timeout, args.variants)
    else:
        parser.error("--manifest and --output are required")
