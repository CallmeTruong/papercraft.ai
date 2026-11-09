"""Local editor service with optimistic revisions and atomic background-job commits."""

import base64
import copy
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .export import assert_exportable, dxf_document, export_bundle, geometry_path, svg_document
from .fabrication.models import PrintLayout, ScenePlan
from .fabrication.printing import print_pdf
from .imaging import normalize_image
from .models import DesignConfig, DesignError, FabricationProfile
from .pipeline import edit_design, generate_design, png_bytes, restore_design, validate_design
from .workflows import capabilities, settings


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision_id: str
    config: DesignConfig | None = None
    profile: FabricationProfile | None = None
    depths: dict[int, int] | None = None
    auto_repair: bool = False
    production: bool = True
    source_lighting: bool = True
    paper_transmittance: float = Field(default=0.55, gt=0, le=1)
    scene_plan: ScenePlan | None = None
    source_distance_mm: float = Field(default=25, ge=5, le=100)
    light_diffusion_mm: float = Field(default=6, ge=0, le=30)
    time_limit_seconds: int = Field(default=180, ge=30, le=1800, strict=True)


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision_id: str
    operation: str
    payload: dict = {}


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str
    format: str = "zip"
    intent: str = "draft"
    layer_id: str | None = None
    layout: PrintLayout = PrintLayout()


def create_app(storage: Path | str = ".papercraft"):
    storage = Path(storage)
    storage.mkdir(parents=True, exist_ok=True)
    # Geometry and local inference both consume substantial CPU/RAM. Serialize
    # heavy jobs on this local workstation; API reads remain responsive.
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="papercraft")
    lock = threading.RLock()
    projects, jobs = {}, {}

    @asynccontextmanager
    async def lifespan(app):
        yield
        pool.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="PaperCraft AI — local CPU studio", lifespan=lifespan)

    @app.get("/api/capabilities")
    def supported_capabilities():
        return {
            **capabilities(),
            "config_fields": sorted(DesignConfig.model_fields),
            "light_preview_version": 3,
        }

    @app.get("/api/models")
    def model_status():
        from .ai.setup import availability

        return availability()

    @app.exception_handler(DesignError)
    async def design_error(request, exc):
        status = 409 if exc.code in ("STALE_REVISION", "INVALID_GEOMETRY") else 422
        return JSONResponse(
            {"error": {"code": exc.code, "message": str(exc), "retryable": False}}, status
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {"error": {"code": "INVALID_INPUT", "message": str(exc), "retryable": False}}, 422
        )

    @app.exception_handler(OSError)
    async def storage_error(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": "STORAGE_FAILED",
                    "message": "Cannot save project. The previous revision has been retained.",
                    "retryable": True,
                }
            },
            503,
        )

    def save(project):
        snapshot = {
            "id": project["id"],
            "revision_id": project["revision_id"],
            "config": project["config"].model_dump(),
            "source_base64": base64.b64encode(project["source"]).decode(),
            "design": project["design"].snapshot() if project["design"] else None,
        }
        destination = storage / f"{project['id']}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, allow_nan=False), encoding="utf-8")
        temporary.replace(destination)

    def get_project(project_id):
        if len(project_id) != 32 or any(c not in "0123456789abcdef" for c in project_id):
            raise HTTPException(404, "Project not found")
        with lock:
            if project_id not in projects:
                path = storage / f"{project_id}.json"
                if not path.exists():
                    raise HTTPException(404, "Project not found")
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                design = restore_design(snapshot["design"]) if snapshot["design"] else None
                projects[project_id] = {
                    "id": project_id,
                    "revision_id": snapshot["revision_id"],
                    "source": base64.b64decode(snapshot["source_base64"]),
                    "config": DesignConfig(**snapshot["config"]),
                    "design": design,
                    "history": [],
                    "redo": [],
                }
            return projects[project_id]

    def summary(project):
        design = project["design"]
        data = {
            "id": project["id"],
            "revision_id": project["revision_id"],
            "config": project["config"].model_dump(),
            "layers": [],
            "report": None,
            "report_current": False,
            "metrics": {},
            "can_undo": bool(project["history"]),
            "can_redo": bool(project["redo"]),
        }
        if design:
            data.update(
                {
                    "width_mm": design.width_mm,
                    "height_mm": design.height_mm,
                    "profile": design.profile.model_dump(),
                    "frame": design.frame.spec.model_dump(),
                    "workflow": settings(design.config).model_dump(),
                    "scene_plan": design.scene_plan.model_dump() if design.scene_plan else None,
                    "material_accounting": design.material_accounting(),
                    "production_receipts": design.production_receipts,
                    "light_plan": design.light_plan,
                    "fabrication_state": "ready"
                    if design.report_is_current() and design.report["status"] == "PASS"
                    else "draft_unfabricable",
                    "legacy_requires_conversion": design.legacy_requires_conversion,
                    "report": design.report,
                    "report_current": design.report_is_current(),
                    "metrics": design.abstraction.metrics,
                    "regions": design.abstraction.region_info,
                    "depth_assignments": design.depth_assignments,
                    "repairs": [
                        {
                            "index": index,
                            **{
                                key: value
                                for key, value in record.items()
                                if key
                                not in (
                                    "before_wkt",
                                    "after_wkt",
                                    "validation_after",
                                    "current_footprint_wkt",
                                )
                            },
                        }
                        for index, record in enumerate(design.repairs)
                    ],
                }
            )
            from shapely.geometry import mapping

            for layer, geometry in zip(design.layers, design.geometries):
                data["layers"].append(
                    {
                        "id": layer.id,
                        "color": layer.color,
                        "index": layer.index,
                        "spacing_mm": layer.spacing_mm,
                        "attachment": layer.attachment,
                        "depth_source": layer.depth_source,
                        "region_ids": layer.region_ids,
                        "path": geometry_path(geometry),
                        "geometry": mapping(geometry),
                    }
                )
        return data

    def require_revision(project, revision):
        if project["revision_id"] != revision:
            raise DesignError("Project changed; reload the current revision", "STALE_REVISION")

    def commit(project, design, *, require_ready=False):
        if require_ready and (not design.report_is_current() or design.report["status"] != "PASS"):
            raise DesignError(
                "Production generation cannot publish an uncertified design",
                "INFEASIBLE_CONSTRAINTS",
            )
        candidate = {**project, "history": list(project["history"])}
        if candidate["design"]:
            candidate["history"].append(candidate["design"])
            candidate["history"] = candidate["history"][-20:]
        candidate.update(
            design=design, revision_id=design.revision_id, config=design.config, redo=[]
        )
        save(candidate)
        project.update(candidate)

    def launch(project, revision, compute, apply=None, *, with_progress=False):
        with lock:
            if sum(j["state"] in ("queued", "running") for j in jobs.values()) >= 8:
                raise DesignError("Too many pending jobs", "RESOURCE_LIMIT")
            completed = [
                key for key, value in jobs.items() if value["state"] not in ("queued", "running")
            ]
            for key in completed[:-20]:
                jobs.pop(key, None)
            job_id = uuid4().hex
            job = {
                "id": job_id,
                "project_id": project["id"],
                "input_revision_id": revision,
                "state": "queued",
                "progress": 0,
                "error": None,
                "stage": "queued",
                "created_at": time.time(),
            }
            jobs[job_id] = job

        def work():
            with lock:
                if job["state"] == "cancelled":
                    return
                job.update(state="running", progress=0.01, stage="starting", started_at=time.time())
            try:

                def update_progress(stage, value):
                    with lock:
                        if job["state"] == "cancelled":
                            raise DesignError("Job cancelled", "JOB_CANCELLED")
                        require_revision(project, revision)
                        job.update(stage=stage, progress=value, updated_at=time.time())

                value = compute(update_progress) if with_progress else compute()
                with lock:
                    if job["state"] == "cancelled":
                        return
                    require_revision(project, revision)
                    if apply:
                        apply(value)
                    else:
                        job["download"] = value
                    job.update(
                        state="succeeded",
                        progress=1,
                        stage="complete",
                        finished_at=time.time(),
                        result_revision_id=project["revision_id"],
                    )
            except Exception as exc:  # noqa: BLE001 -- worker boundary must persist unexpected failures.
                with lock:
                    if job["state"] != "cancelled":
                        job.update(
                            state="failed",
                            finished_at=time.time(),
                            error={
                                "code": getattr(exc, "code", "PROCESSING_FAILED"),
                                "message": str(exc),
                                "retryable": False,
                            },
                        )

        pool.submit(work)
        return {"job_id": job_id, "input_revision_id": revision}

    @app.post("/projects", status_code=201)
    async def upload(image: Annotated[UploadFile, File()], config: Annotated[str, Form()] = "{}"):
        try:
            settings = DesignConfig(**json.loads(config))
        except (ValueError, TypeError) as exc:
            raise DesignError(f"Invalid config: {exc}") from exc
        source = await image.read(30_000_001)
        if len(source) > 30_000_000:
            raise DesignError("Image upload exceeds 30 MB", "RESOURCE_LIMIT")
        normalize_image(source, settings.max_side, settings.backing)
        project = {
            "id": uuid4().hex,
            "revision_id": uuid4().hex,
            "source": source,
            "config": settings,
            "design": None,
            "history": [],
            "redo": [],
        }
        with lock:
            projects[project["id"]] = project
            save(project)
            return summary(project)

    @app.post("/projects/import", status_code=201)
    def import_project(snapshot: dict):
        design = restore_design(snapshot)
        project = {
            "id": uuid4().hex,
            "revision_id": design.revision_id,
            "source": design.source_bytes,
            "config": design.config,
            "design": design,
            "history": [],
            "redo": [],
        }
        with lock:
            projects[project["id"]] = project
            save(project)
            return summary(project)

    @app.post("/references/forest-deer", status_code=201)
    def reference(mode: str = "multicolor"):
        if mode not in ("multicolor", "lightbox"):
            raise DesignError("Reference mode must be multicolor or lightbox")
        from .fabrication.reference import forest_reference

        design = forest_reference(mode)
        project = {
            "id": uuid4().hex,
            "revision_id": design.revision_id,
            "source": design.source_bytes,
            "config": design.config,
            "design": design,
            "history": [],
            "redo": [],
        }
        with lock:
            save(project)
            projects[project["id"]] = project
            return summary(project)

    @app.get("/projects/{project_id}")
    def project_summary(project_id: str):
        with lock:
            return summary(get_project(project_id))

    @app.get("/projects/{project_id}/source")
    def project_source(project_id: str):
        return Response(
            png_bytes(normalize_image(get_project(project_id)["source"], max_side=1024)),
            media_type="image/png",
        )

    @app.get("/projects/{project_id}/light-plan")
    def light_plan_preview(
        project_id: str,
        transmittance: float = 0.55,
        visible: str | None = None,
        overlap_only: bool = False,
    ):
        design = get_project(project_id)["design"]
        if design is None:
            raise DesignError("Generate a design first")
        from .fabrication.light import overlap_profile, resolved_light_plan

        overlap = overlap_profile(
            design,
            transmittance=transmittance,
            visible=[item for item in visible.split(",") if item] if visible is not None else None,
        )
        if overlap_only:
            return {"overlap": overlap}
        plan = resolved_light_plan(design)
        return {
            **plan,
            "overlap": overlap,
            "preview_only": not design.light_plan or design.light_plan.get("planning_version") != 2,
            "coordinate_origin": "top-left outer sheet; x right, y down",
            "stack_depth_mm": sum(
                layer.spacing_mm + design.profile.thickness_mm for layer in design.layers
            ),
            "hardware_note": "Zone centers refer to an effective colored source behind the rear diffuser. White LED panel plus broad printed filter is the exported option; RGB emitters require separate calibration and a neutral diffuser.",
        }

    @app.get("/projects/{project_id}/light-preview/{layer_id}")
    def light_preview(
        project_id: str,
        layer_id: str,
        transmittance: float = 0.55,
        visible: str | None = None,
        revision_id: str | None = None,
        source_distance_mm: float | None = None,
        diffusion_mm: float | None = None,
        uniform_color: str | None = None,
        source_gain: float = 1.0,
    ):
        project = get_project(project_id)
        if revision_id:
            require_revision(project, revision_id)
        design = project["design"]
        if design is None:
            raise DesignError("Generate a design first")
        from .fabrication.light import cached_light_fields

        fields = cached_light_fields(
            design,
            transmittance=transmittance,
            visible=[item for item in visible.split(",") if item] if visible is not None else None,
            source_distance_mm=source_distance_mm,
            diffusion_mm=diffusion_mm,
            uniform_color=uniform_color,
            source_gain=source_gain,
        )
        if layer_id not in fields:
            raise DesignError("Unknown or hidden lighting layer")
        return Response(
            png_bytes(fields[layer_id]),
            media_type="image/png",
            headers={
                "X-PaperCraft-Optics": "approximate-broad-light-zones; material calibration NOT_RUN"
            },
        )

    @app.post("/projects/{project_id}/generate", status_code=202)
    def generate(project_id: str, request: GenerationRequest):
        with lock:
            project = get_project(project_id)
            require_revision(project, request.expected_revision_id)
            config = request.config or project["config"]
            profile = request.profile or (
                project["design"].profile if project["design"] else FabricationProfile()
            )
            source = project["source"]
            scene_plan = (
                request.scene_plan
                if "scene_plan" in request.model_fields_set
                else (project["design"].scene_plan if project["design"] else None)
            )
            return launch(
                project,
                request.expected_revision_id,
                lambda progress: generate_design(
                    source,
                    config,
                    profile,
                    request.depths,
                    request.auto_repair,
                    scene_plan=scene_plan,
                    progress=progress,
                    production=request.production,
                    source_lighting=request.source_lighting,
                    paper_transmittance=request.paper_transmittance,
                    source_distance_mm=request.source_distance_mm,
                    light_diffusion_mm=request.light_diffusion_mm,
                    time_limit_seconds=request.time_limit_seconds,
                ),
                lambda design: commit(project, design, require_ready=request.production),
                with_progress=True,
            )

    @app.post("/projects/{project_id}/compare", status_code=202)
    def compare_project(project_id: str, request: GenerationRequest):
        from .comparison import compare, write_json

        with lock:
            project = get_project(project_id)
            require_revision(project, request.expected_revision_id)
            if request.scene_plan or (project["design"] and project["design"].scene_plan):
                raise DesignError(
                    "Photo comparison requires an image-generated project, not an authored ScenePlan"
                )
            if request.depths is not None or (
                project["design"] and project["design"].depth_assignments is not None
            ):
                raise DesignError(
                    "Manual region-depth assignments cannot be transferred across model region partitions; compare an unassigned image"
                )
            config = request.config or project["config"]
            profile = request.profile or (
                project["design"].profile if project["design"] else FabricationProfile()
            )
            comparison_id = uuid4().hex
            directory = storage / "comparisons" / project_id / comparison_id
            source = project["source"]
            light_plan = project["design"].light_plan if project["design"] else None
            generation_options = {
                "source_lighting": request.source_lighting
                if "source_lighting" in request.model_fields_set or not project["design"]
                else bool(light_plan),
                "paper_transmittance": request.paper_transmittance
                if "paper_transmittance" in request.model_fields_set or not light_plan
                else light_plan["paper_transmittance_assumption"],
            }

            for key, plan_key in (
                ("source_distance_mm", "source_distance_mm"),
                ("light_diffusion_mm", "diffusion_mm_assumption"),
            ):
                if key in request.model_fields_set:
                    generation_options[key] = getattr(request, key)
                elif light_plan and light_plan.get("planning_version") == 2:
                    generation_options[key] = light_plan[plan_key]

            def compute(progress):
                report = compare(
                    source,
                    config,
                    profile,
                    directory,
                    progress=progress,
                    generation_options=generation_options,
                    timeout=request.time_limit_seconds,
                )
                report.update(
                    comparison_id=comparison_id,
                    project_id=project_id,
                    source_revision_id=request.expected_revision_id,
                )
                return report

            def publish(report):
                directory.mkdir(parents=True, exist_ok=True)
                report["api_published"] = True
                write_json(directory / "results.json", report)
                write_json(directory.parent / "latest.json", {"comparison_id": comparison_id})

            started = launch(
                project, request.expected_revision_id, compute, publish, with_progress=True
            )
            return {**started, "comparison_id": comparison_id}

    def comparison_directory(project_id, comparison_id):
        project = get_project(project_id)
        if not re.fullmatch(r"[a-f0-9]{32}", comparison_id):
            raise HTTPException(404, "Comparison not found")
        directory = storage / "comparisons" / project_id / comparison_id
        if not (directory / "results.json").is_file():
            raise HTTPException(404, "Comparison not found")
        data = json.loads((directory / "results.json").read_text(encoding="utf8"))
        if not data.get("api_published") or data.get("comparison_id") != comparison_id:
            raise HTTPException(404, "Comparison has not been published")
        return project, directory

    @app.get("/projects/{project_id}/comparisons/latest")
    def latest_comparison(project_id: str):
        get_project(project_id)
        path = storage / "comparisons" / project_id / "latest.json"
        if not path.is_file():
            raise HTTPException(404, "No comparison yet")
        return comparison_result(project_id, json.loads(path.read_text())["comparison_id"])

    @app.get("/projects/{project_id}/comparisons/{comparison_id}")
    def comparison_result(project_id: str, comparison_id: str):
        with lock:
            project, directory = comparison_directory(project_id, comparison_id)
            report = json.loads((directory / "results.json").read_text(encoding="utf8"))
            return {**report, "stale": report["source_revision_id"] != project["revision_id"]}

    @app.get("/projects/{project_id}/comparisons/{comparison_id}/{variant}/{artifact}")
    def comparison_artifact(project_id: str, comparison_id: str, variant: str, artifact: str):
        _, directory = comparison_directory(project_id, comparison_id)
        if variant not in ("cpu", "model") or not re.fullmatch(
            r"(?:(?:source|proposal|cut|sheets|sheet-[0-9]{2})\.png|project\.json|cut-ready\.zip)",
            artifact,
        ):
            raise HTTPException(404, "Comparison artifact not found")
        path = directory / variant / artifact
        if not path.is_file():
            raise HTTPException(404, "Comparison artifact not found")
        if artifact.endswith(".png"):
            return FileResponse(path, media_type="image/png")
        result = json.loads((directory / variant / "result.json").read_text(encoding="utf8"))
        if not result.get("cut_ready"):
            raise DesignError("This comparison candidate is not cut-ready")
        return FileResponse(
            path,
            media_type="application/zip" if artifact.endswith(".zip") else "application/json",
            filename=f"{variant}-{artifact}",
        )

    @app.post("/projects/{project_id}/edits")
    def edit(project_id: str, request: EditRequest):
        with lock:
            project = get_project(project_id)
            require_revision(project, request.expected_revision_id)
            if not project["design"]:
                raise DesignError("Generate the design before editing")
            try:
                if request.operation in ("undo", "redo"):
                    candidate = {
                        **project,
                        "history": list(project["history"]),
                        "redo": list(project["redo"]),
                    }
                    backwards = request.operation == "undo"
                    source_stack = candidate["history"] if backwards else candidate["redo"]
                    target_stack = candidate["redo"] if backwards else candidate["history"]
                    if not source_stack:
                        raise DesignError("No revision to restore")
                    target_stack.append(project["design"])
                    design = copy.deepcopy(source_stack.pop())
                    design.parent_revision_id = project["revision_id"]
                    design.revision_id = uuid4().hex
                    design.report = None
                    candidate.update(
                        design=design, revision_id=design.revision_id, config=design.config
                    )
                    save(candidate)
                    project.update(candidate)
                else:
                    commit(
                        project, edit_design(project["design"], request.operation, request.payload)
                    )
            except (ValidationError, KeyError, TypeError) as exc:
                raise DesignError(f"Invalid edit: {exc}") from exc
            return summary(project)

    @app.post("/projects/{project_id}/validate", status_code=202)
    def validate(project_id: str, request: GenerationRequest):
        with lock:
            project = get_project(project_id)
            require_revision(project, request.expected_revision_id)
            design = project["design"]
            if not design:
                raise DesignError("Generate a design first")

            def apply(report):
                design.report = report
                save(project)

            return launch(
                project, request.expected_revision_id, lambda: validate_design(design), apply
            )

    @app.get("/projects/{project_id}/snapshot")
    def snapshot(project_id: str):
        with lock:
            design = get_project(project_id)["design"]
            if not design:
                raise DesignError("Generate a design before saving the portable snapshot")
            return design.snapshot()

    @app.post("/projects/{project_id}/exports", status_code=202)
    def export(project_id: str, request: ExportRequest):
        with lock:
            project = get_project(project_id)
            require_revision(project, request.revision_id)
            design = project["design"]
            if not design:
                raise DesignError("Generate a design first")
            assert_exportable(design, request.intent)
            if request.format not in (
                "zip",
                "pdf",
                "light_filter",
                "png",
                "project",
                "report",
                "svg",
                "cut_svg",
                "dxf",
            ):
                raise DesignError("Unsupported export format")
            if request.format in ("svg", "cut_svg", "dxf") and request.layer_id not in [
                l.id for l in design.layers
            ]:
                raise DesignError("Select a layer for SVG or DXF export")

            def compute():
                extension, content_type = "zip", "application/zip"
                if request.format == "zip":
                    data = export_bundle(design, request.intent, request.layout)
                elif request.format == "pdf":
                    data = print_pdf(
                        [(l.id, g) for l, g in zip(design.layers, design.geometries)],
                        design.width_mm,
                        design.height_mm,
                        request.layout,
                        status=request.intent.upper(),
                    )
                    extension, content_type = "pdf", "application/pdf"
                elif request.format == "light_filter":
                    from .fabrication.light import color_diffuser_pdf

                    data = color_diffuser_pdf(design, request.layout)
                    extension, content_type = "pdf", "application/pdf"
                elif request.format == "png":
                    data, extension, content_type = (
                        png_bytes(design.composite()),
                        "png",
                        "image/png",
                    )
                elif request.format in ("project", "report"):
                    value = (
                        design.snapshot()
                        if request.format == "project"
                        else (design.report if design.report_is_current() else {"status": "STALE"})
                    )
                    data, extension, content_type = (
                        json.dumps(value, indent=2).encode(),
                        "json",
                        "application/json",
                    )
                else:
                    index = next(i for i, l in enumerate(design.layers) if l.id == request.layer_id)
                    layer, geometry = design.layers[index], design.geometries[index]
                    text = (
                        svg_document(
                            geometry,
                            design.width_mm,
                            design.height_mm,
                            layer.color,
                            layer.id,
                            cut_only=request.format == "cut_svg",
                        )
                        if request.format in ("svg", "cut_svg")
                        else dxf_document(geometry, design.height_mm)
                    )
                    data, extension = (
                        text.encode(),
                        "svg" if request.format == "cut_svg" else request.format,
                    )
                    content_type = "image/svg+xml" if extension == "svg" else "application/dxf"
                return {
                    "data": data,
                    "content_type": content_type,
                    "filename": f"papercraft-{request.intent}.{extension}",
                }

            return launch(project, request.revision_id, compute)

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str):
        with lock:
            if job_id not in jobs:
                raise HTTPException(404, "Job not found")
            job = jobs[job_id]
            return {
                **{k: v for k, v in job.items() if k != "download"},
                "elapsed_seconds": max(
                    0,
                    job.get("finished_at", time.time()) - job.get("started_at", job["created_at"]),
                ),
            }

    @app.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        with lock:
            if job_id not in jobs:
                raise HTTPException(404, "Job not found")
            if jobs[job_id]["state"] in ("queued", "running"):
                jobs[job_id]["state"] = "cancelled"
                jobs[job_id]["finished_at"] = time.time()
            return job_status(job_id)

    @app.get("/jobs/{job_id}/download")
    def download(job_id: str):
        with lock:
            if (
                job_id not in jobs
                or jobs[job_id]["state"] != "succeeded"
                or "download" not in jobs[job_id]
            ):
                raise HTTPException(404, "Export is not available")
            artifact = jobs[job_id]["download"]
            return Response(
                artifact["data"],
                media_type=artifact["content_type"],
                headers={"Content-Disposition": f'attachment; filename="{artifact["filename"]}"'},
            )

    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.exists():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(frontend / "index.html")
    else:

        @app.get("/")
        def index():
            return {
                "message": "Build the editor: cd frontend && npm install && npm run build",
                "api_docs": "/docs",
            }

    return app
