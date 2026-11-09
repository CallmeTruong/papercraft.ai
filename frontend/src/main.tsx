import {
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowDown,
  ArrowUp,
  Box,
  Check,
  ChevronDown,
  Download,
  Eye,
  EyeOff,
  FileUp,
  ImagePlus,
  Layers,
  Lightbulb,
  LoaderCircle,
  RotateCcw,
  ScanLine,
  ShieldCheck,
  Sparkles,
  Undo2,
  Redo2,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
const Scene = lazy(() =>
  import("./Scene").then((module) => ({ default: module.Scene })),
);
import "@fontsource/dm-sans/latin-400.css";
import "@fontsource/dm-sans/latin-ext-400.css";
import "@fontsource/dm-sans/latin-600.css";
import "@fontsource/dm-sans/latin-ext-600.css";
import "@fontsource/manrope/latin-600.css";
import "@fontsource/manrope/latin-800.css";
import type { Config, Project, Finding, Workflow } from "./types";
import "./style.css";
import { ScenePlanner } from "./ScenePlanner";
import { FlatPreview } from "./FlatPreview";
import { framePalette, type FrameKind } from "./presentation";
import { LightPalette, TransmissionPanel } from "./LightPalette";
import { DetailRegions } from "./DetailRegions";

const defaults: Config = {
  mode: "multicolor",
  colors: 6,
  layers: 8,
  detail: 0.6,
  palette: null,
  seed: 42,
  max_side: 384,
  width_mm: 200,
  height_mm: null,
  spacing_mm: 3,
  assembly_mode: "frame_connected",
  backing: "#ffffff",
  frame_mm: 10,
  artwork_fit: "contain",
  ai: {
    segmentation: true,
    face_features: true,
    depth: true,
    important_details: true,
  },
  workflow: {
    family: "multicolor",
    style: "colorblock",
    assembly: "flat",
    lighting: "ambient",
  },
};

function preferredAI() {
  try {
    const saved = JSON.parse(
      localStorage.getItem("papercraft-ai-settings") || "null",
    );
    if (
      saved &&
      ["segmentation", "face_features", "depth"].every(
        (key) => typeof saved[key] === "boolean",
      )
    )
      return {
        important_details: Boolean(
          saved.segmentation || saved.face_features || saved.depth,
        ),
        ...saved,
      } as NonNullable<Config["ai"]>;
  } catch {
    /* Use AI-first defaults when the local preference is invalid. */
  }
  return defaults.ai;
}

async function request(path: string, options: RequestInit = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      data.error?.message || data.detail || "Unable to complete this action.",
    );
  return data;
}
const json = (value: unknown) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});

const restartBackendMessage =
  "Backend update required. Restart after active jobs finish, then refresh.";
function supportsDetailConfig(capabilities: { config_fields?: string[] }) {
  return ["detail_boxes", "detail_policy", "depth_paper"].every((key) =>
    capabilities.config_fields?.includes(key),
  );
}

function App() {
  const [backendReady, setBackendReady] = useState<boolean | null>(null);
  const [lightPreviewReady, setLightPreviewReady] = useState<boolean | null>(
    null,
  );
  useEffect(() => {
    request("/api/capabilities")
      .then((c) => {
        setBackendReady(supportsDetailConfig(c));
        setLightPreviewReady(c.light_preview_version >= 3);
      })
      .catch(() => {});
  }, []);
  async function requireCurrentBackend() {
    const capabilities = await request("/api/capabilities");
    const supported = supportsDetailConfig(capabilities);
    setBackendReady(supported);
    if (!supported) throw new Error(restartBackendMessage);
    if (
      config.ai?.important_details &&
      !capabilities.ai_config_fields?.includes("important_details")
    )
      throw new Error(
        "Restart the backend to enable automatic detail protection.",
      );
  }
  const [project, setProject] = useState<Project | null>(null);
  const [config, setConfig] = useState<Config>(() => ({
    ...defaults,
    ai: preferredAI(),
  }));
  useEffect(() => {
    if (config.ai)
      localStorage.setItem("papercraft-ai-settings", JSON.stringify(config.ai));
  }, [config.ai]);
  const [frameKind, setFrameKind] = useState<FrameKind>(() => {
    const saved = localStorage.getItem("papercraft-display-frame");
    return ["auto", "walnut", "oak", "white", "none"].includes(saved || "")
      ? (saved as FrameKind)
      : "auto";
  });
  const [timeLimit, setTimeLimit] = useState(() => {
    const saved = Number(localStorage.getItem("papercraft-time-limit"));
    return Number.isInteger(saved) && saved >= 30 && saved <= 1800
      ? saved
      : 180;
  });
  useEffect(
    () => localStorage.setItem("papercraft-display-frame", frameKind),
    [frameKind],
  );
  useEffect(() => {
    if (Number.isInteger(timeLimit) && timeLimit >= 30 && timeLimit <= 1800)
      localStorage.setItem("papercraft-time-limit", String(timeLimit));
  }, [timeLimit]);
  const [modelStatus, setModelStatus] = useState<
    Record<string, { status: string }>
  >({});
  useEffect(() => {
    request("/api/models")
      .then(setModelStatus)
      .catch(() => setModelStatus({}));
  }, []);
  const [busy, setBusy] = useState("");
  const [jobId, setJobId] = useState("");
  const [jobDetail, setJobDetail] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [view, setView] = useState("flat");
  const [visible, setVisible] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [focusedIssue, setFocusedIssue] = useState<Finding | null>(null);
  const [tab, setTab] = useState("layers");
  const [zoom, setZoom] = useState(1);
  const [showShadows, setShowShadows] = useState(true);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [intensity, setIntensity] = useState(2);
  const [lightColor, setLightColor] = useState("#ffe0a3");
  const [lightMode, setLightMode] = useState("source");
  const [photoOverlayReady, setPhotoOverlayReady] = useState(false);
  const [photoOverlay, setPhotoOverlay] = useState(
    () => localStorage.getItem("papercraft-photo-overlay-v2") !== "false",
  );
  const [photoStrength, setPhotoStrength] = useState(1);
  useEffect(() => {
    request("/api/capabilities")
      .then((c) => setPhotoOverlayReady(c.photo_overlay_preview === true))
      .catch(() => {});
  }, []);
  useEffect(() => {
    localStorage.setItem("papercraft-photo-overlay-v2", String(photoOverlay));
  }, [photoOverlay]);
  const [transmittance, setTransmittance] = useState(0.55);
  const [sourceDistance, setSourceDistance] = useState(25);
  const [lightDiffusion, setLightDiffusion] = useState(6);
  const [paletteText, setPaletteText] = useState("");
  const [printPaper, setPrintPaper] = useState("A4");
  const [printOrientation, setPrintOrientation] = useState("auto");
  const [printSide, setPrintSide] = useState("front");
  const [sceneText, setSceneText] = useState("");
  const workflow: Workflow =
    config.workflow ||
    (config.mode === "lightbox"
      ? {
          family: "depth_scene",
          style: "landscape",
          assembly: "spaced",
          lighting: "backlit",
        }
      : {
          family: "multicolor",
          style: "colorblock",
          assembly: config.spacing_mm > 0 ? "spaced" : "flat",
          lighting: "ambient",
        });
  function chooseFamily(family: Workflow["family"]) {
    setSceneText("");
    setDepthMode("heuristic");
    setDepths({});
    setConfig((prev) => ({
      ...prev,
      mode: family === "depth_scene" ? "lightbox" : "multicolor",
      depth_paper:
        family === "depth_scene"
          ? prev.depth_paper || "multicolor"
          : prev.depth_paper,
      workflow: {
        family,
        style:
          family === "stencil"
            ? "bold"
            : family === "multicolor"
              ? "colorblock"
              : "landscape",
        assembly: family === "depth_scene" ? "spaced" : "flat",
        lighting: "ambient",
      },
    }));
  }
  const [depthMode, setDepthMode] = useState("heuristic");
  const [depths, setDepths] = useState<Record<number, number>>({});
  const fileInput = useRef<HTMLInputElement>(null);
  const projectInput = useRef<HTMLInputElement>(null);
  const currentJob = useRef("");
  const dragging = useRef<{
    x: number;
    y: number;
    px: number;
    py: number;
  } | null>(null);

  function apply(data: Project) {
    setProject(data);
    if (data.light_plan) {
      setLightMode(
        data.light_plan.mode === "uniform_transmission" ? "uniform" : "source",
      );
      setTransmittance(data.light_plan.paper_transmittance_assumption);
      if (data.light_plan.planning_version === 2) {
        setSourceDistance(data.light_plan.source_distance_mm ?? 25);
        setLightDiffusion(data.light_plan.diffusion_mm_assumption ?? 6);
      }
    }
    setSceneText(
      data.scene_plan ? JSON.stringify(data.scene_plan, null, 2) : "",
    );
    setConfig({
      ...data.config,
      ai: data.config.ai === undefined ? preferredAI() : data.config.ai,
    });
    setVisible(new Set(data.layers.map((layer) => layer.id)));
    setPaletteText(data.config.palette?.join(", ") || "");
    localStorage.setItem("papercraft-project", data.id);
    const assignments: Record<number, number> = {};
    data.layers.forEach((layer) =>
      layer.region_ids.forEach((id) => {
        assignments[id] = layer.index;
      }),
    );
    setDepths(data.depth_assignments || assignments);
    setDepthMode(data.depth_assignments ? "manual" : "heuristic");
  }

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get(
      "project",
    );
    const id =
      requested && /^[a-f0-9]{32}$/.test(requested)
        ? requested
        : localStorage.getItem("papercraft-project");
    if (id)
      request(`/projects/${id}`)
        .then(apply)
        .catch(() => localStorage.removeItem("papercraft-project"));
  }, []);

  function patch<K extends keyof Config>(key: K, value: Config[K]) {
    setConfig((prev) => ({ ...prev, [key]: value }));
  }

  async function run(action: () => Promise<void>, label: string) {
    if (busy) return;
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy("");
      setJobId("");
      setJobDetail("");
      currentJob.current = "";
    }
  }

  async function poll(id: string) {
    setJobId(id);
    currentJob.current = id;
    while (true) {
      const job = await request(`/jobs/${id}`);
      const [stage, layer, substage] = String(job.stage || "starting").split(
        ":",
      );
      const stages: Record<string, string> = {
        queued: "Queued",
        starting: "Starting",
        image_abstraction: "Analyzing image",
        construct_layers: "Building layers",
        vectorize: "Tracing cut paths",
        fabricate: "Connecting sheets",
        validate: "Checking geometry",
        repair: "Repairing supports",
        save: "Saving design",
        complete: "Complete",
      };
      const subdetail = substage?.startsWith("support-")
        ? `Bridge ${substage.slice(8)}`
        : substage?.startsWith("attempt-")
          ? `Candidate ${substage.slice(8)}`
          : substage === "check"
            ? "Checking layer"
            : "";
      setJobDetail(
        `${stages[stage] || stage}${layer ? ` · ${layer}` : ""}${subdetail ? ` · ${subdetail}` : ""} · ${Math.round((job.progress || 0) * 100)}% · ${Math.floor(job.elapsed_seconds || 0)} s`,
      );
      if (job.state === "succeeded") return job;
      if (job.state === "failed") throw new Error(job.error.message);
      if (job.state === "cancelled")
        throw new Error("Cancelled. Previous design preserved.");
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }

  async function generate(
    data: Project,
    override?: Config,
    freshUpload = false,
  ) {
    await requireCurrentBackend();
    const current = override || config;
    const settings = {
      ...current,
      depth_paper:
        current.depth_paper ||
        (workflow.family === "depth_scene"
          ? workflow.lighting === "backlit"
            ? "white"
            : "multicolor"
          : null),
      palette: paletteText.trim()
        ? paletteText.split(",").map((color) => color.trim())
        : null,
    };
    const job = await request(
      `/projects/${data.id}/generate`,
      json({
        expected_revision_id: data.revision_id,
        config: settings,
        production: true,
        time_limit_seconds: timeLimit,
        source_lighting: lightMode === "source",
        paper_transmittance: transmittance,
        source_distance_mm: sourceDistance,
        light_diffusion_mm: lightDiffusion,
        scene_plan:
          !freshUpload && sceneText.trim() ? JSON.parse(sceneText) : null,
        depths:
          !freshUpload && config.mode === "lightbox" && depthMode === "manual"
            ? depths
            : null,
      }),
    );
    await poll(job.job_id);
    apply(await request(`/projects/${data.id}`));
    setNotice("Design ready · Geometry checks passed");
    setSelected(null);
    setFocusedIssue(null);
  }

  async function upload(file: File) {
    await requireCurrentBackend();
    const fresh = { ...config, detail_boxes: [] };
    const form = new FormData();
    form.append("image", file);
    form.append("config", JSON.stringify(fresh));
    const data = await request("/projects", { method: "POST", body: form });
    apply(data);
    setSceneText("");
    setDepthMode("heuristic");
    setDepths({});
    await generate(data, fresh, true);
  }

  async function edit(operation: string, payload: object = {}) {
    if (!project) return;
    const data = await request(
      `/projects/${project.id}/edits`,
      json({ expected_revision_id: project.revision_id, operation, payload }),
    );
    apply(data);
    setFocusedIssue(null);
  }

  async function validate() {
    const job = await request(
      `/projects/${project!.id}/validate`,
      json({ expected_revision_id: project!.revision_id }),
    );
    await poll(job.job_id);
    apply(await request(`/projects/${project!.id}`));
    setNotice("Validation updated");
  }

  async function download(format: string, intent = "draft") {
    const job = await request(
      `/projects/${project!.id}/exports`,
      json({
        revision_id: project!.revision_id,
        format,
        layout: {
          paper: printPaper,
          orientation: printOrientation,
          print_side: printSide,
        },
        intent,
        layer_id: selected || project!.layers[0]?.id,
      }),
    );
    await poll(job.job_id);
    const a = document.createElement("a");
    a.href = `/jobs/${job.job_id}/download`;
    a.click();
    setNotice("Download ready");
  }

  const layers = project?.layers || [];
  const findings =
    project?.report?.layers.flatMap((report) => report.findings) || [];
  const status = !layers.length
    ? "No design"
    : !project?.report_current
      ? "Recheck required"
      : project?.report?.status || "Not checked";
  const currentPass = project?.report_current && status === "PASS";
  const dirty =
    project && JSON.stringify(project.config) !== JSON.stringify(config);
  const disabled = !!busy;

  return (
    <div className="app">
      {backendReady === false && (
        <div role="alert" className="notification error">
          {restartBackendMessage}
        </div>
      )}
      <header className="topbar">
        <a className="brand" href="/" aria-label="PaperCraft AI">
          <span className="brand-mark">
            <Layers size={23} />
          </span>
          <strong>
            papercraft<span>ai</span>
          </strong>
        </a>
        <div className="top-divider" />
        <span className="workspace-name">
          Design studio <ChevronDown size={13} />
        </span>
        <span className="cpu-chip">
          <i />{" "}
          {config.ai &&
          (config.ai.segmentation ||
            config.ai.depth ||
            config.ai.face_features ||
            config.ai.important_details)
            ? "AI enabled · Local processing"
            : "CPU mode"}
        </span>
        <div className="header-actions">
          <button
            className="icon-button"
            aria-label="Undo"
            disabled={disabled || !project?.can_undo}
            onClick={() => run(() => edit("undo"), "Undoing")}
          >
            <Undo2 size={17} />
          </button>
          <button
            className="icon-button"
            aria-label="Redo"
            disabled={disabled || !project?.can_redo}
            onClick={() => run(() => edit("redo"), "Redoing")}
          >
            <Redo2 size={17} />
          </button>
          <button
            className="button secondary compact"
            disabled={disabled}
            onClick={() => projectInput.current?.click()}
          >
            <FileUp size={15} />
            <span>Open project</span>
          </button>
          <button
            className="button dark compact"
            disabled={disabled || !layers.length}
            onClick={() => run(() => download("project"), "Saving project")}
          >
            <Download size={15} />
            <span>Save project</span>
          </button>
        </div>
      </header>

      <main>
        <section className="intro">
          <div>
            <span className="eyebrow">
              <span /> PAPER, MEET POSSIBILITY
            </span>
            <h1>Turn photos into paper art.</h1>
            <p>Design. Layer. Cut.</p>
          </div>
          <div className="workflow">
            <span className={layers.length ? "done" : "active"}>
              01 <small>Design settings</small>
            </span>
            <b>—</b>
            <span className={layers.length ? "active" : ""}>
              02 <small>Layer design</small>
            </span>
            <b>—</b>
            <span>
              03 <small>Cut & assemble</small>
            </span>
          </div>
        </section>

        {(error || notice || busy) && (
          <div
            role={error ? "alert" : "status"}
            className={`notification ${error ? "error" : ""}`}
          >
            {busy ? (
              <LoaderCircle size={16} className="spin" />
            ) : error ? (
              <ScanLine size={16} />
            ) : (
              <Check size={16} />
            )}
            <div className="notification-content">
              {busy ? (
                `${busy}${jobDetail ? ` — ${jobDetail}` : ""}`
              ) : error.length > 240 ? (
                <>
                  <span>
                    {error.includes("No certified design")
                      ? "No cut-ready design found. Adjust the size or material profile and try again."
                      : "Unable to complete this action."}
                  </span>
                  <details>
                    <summary>Error details</summary>
                    <p>{error}</p>
                  </details>
                </>
              ) : (
                error || notice
              )}
            </div>
            {busy && jobId && (
              <button
                onClick={() =>
                  request(`/jobs/${currentJob.current}/cancel`, {
                    method: "POST",
                  })
                }
              >
                Cancel
              </button>
            )}
            {!busy && (
              <button
                aria-label="Dismiss notification"
                onClick={() => {
                  setError("");
                  setNotice("");
                }}
              >
                <X size={15} />
              </button>
            )}
          </div>
        )}

        <div className="studio">
          <aside className="configuration panel">
            <div className="panel-title">
              <span className="step">01</span>
              <h2>Design settings</h2>
            </div>
            <button
              className={`upload-zone ${project ? "has-image" : ""}`}
              disabled={disabled}
              onClick={() => fileInput.current?.click()}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                const file = event.dataTransfer.files[0];
                if (file && !busy) run(() => upload(file), "Generating design");
              }}
            >
              {project ? (
                <>
                  <img
                    src={`/projects/${project.id}/source`}
                    alt="Source image"
                  />
                  <span>
                    <ImagePlus size={14} /> Replace image
                  </span>
                </>
              ) : (
                <>
                  <span className="upload-icon">
                    <ImagePlus size={25} />
                  </span>
                  <strong>Drop an image here</strong>
                  <small>or click to upload</small>
                  <em>PNG, JPG, WEBP · up to 30 MB</em>
                </>
              )}
            </button>
            <input
              hidden
              ref={fileInput}
              type="file"
              accept="image/*"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) run(() => upload(file), "Generating design");
                e.target.value = "";
              }}
            />
            {project && (
              <DetailRegions
                projectId={project.id}
                config={config}
                disabled={disabled}
                onChange={(boxes) => patch("detail_boxes", boxes)}
                onPolicy={(policy) => patch("detail_policy", policy)}
                automatic={
                  project.metrics.ai_diagnostics?.find(
                    (d) => d.component === "detail_selection",
                  )?.regions || []
                }
                onAdopt={(box) =>
                  patch("detail_boxes", [...(config.detail_boxes || []), box])
                }
              />
            )}
            <input
              hidden
              ref={projectInput}
              type="file"
              accept=".json"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file)
                  run(
                    async () =>
                      apply(
                        await request(
                          "/projects/import",
                          json(JSON.parse(await file.text())),
                        ),
                      ),
                    "Opening project",
                  );
                e.target.value = "";
              }}
            />
            <div className="field-group">
              <label>Design type</label>
              <div className="segmented">
                <button
                  disabled={disabled}
                  className={workflow.family === "stencil" ? "selected" : ""}
                  onClick={() => chooseFamily("stencil")}
                >
                  Stencil
                </button>
                <button
                  disabled={disabled}
                  className={workflow.family === "multicolor" ? "selected" : ""}
                  onClick={() => chooseFamily("multicolor")}
                >
                  Multicolor
                </button>
                <button
                  disabled={disabled}
                  className={
                    workflow.family === "depth_scene" ? "selected" : ""
                  }
                  onClick={() => chooseFamily("depth_scene")}
                >
                  Depth Scene
                </button>
              </div>
              {workflow.family === "depth_scene" && (
                <>
                  <label htmlFor="depth-paper">Depth Scene paper</label>
                  <select
                    id="depth-paper"
                    disabled={disabled}
                    value={
                      config.depth_paper ||
                      (workflow.lighting === "backlit" ? "white" : "multicolor")
                    }
                    onChange={(e) =>
                      patch(
                        "depth_paper",
                        e.target.value as "white" | "multicolor",
                      )
                    }
                  >
                    <option value="white">White paper</option>
                    <option value="multicolor">Colored paper</option>
                  </select>
                </>
              )}
              <label htmlFor="art-style">Style</label>
              <select
                id="art-style"
                disabled={disabled}
                value={workflow.style}
                onChange={(e) =>
                  patch("workflow", {
                    ...workflow,
                    style: e.target.value as Workflow["style"],
                  })
                }
              >
                {workflow.family === "stencil" ? (
                  <>
                    <option value="bold">Bold</option>
                    <option value="detailed">Detailed</option>
                  </>
                ) : workflow.family === "multicolor" ? (
                  <>
                    <option value="colorblock">Colorblock</option>
                    <option value="splash_portrait">
                      Splash Portrait · Custom scene
                    </option>
                  </>
                ) : (
                  <>
                    <option value="landscape">Landscape</option>
                    <option value="portal">Portal · Custom scene</option>
                  </>
                )}
              </select>
            </div>
            <div className="field-group">
              <label htmlFor="colors">
                Paper colors <strong>{config.colors}</strong>
              </label>
              <input
                id="colors"
                type="range"
                min="2"
                max="16"
                value={config.colors}
                disabled={disabled}
                onChange={(e) => patch("colors", +e.target.value)}
              />
              <div className="range-labels">
                <span>Simple</span>
                <span>Rich</span>
              </div>
            </div>
            <div className="field-group">
              <label htmlFor="layer-count">
                Layer limit <strong>{config.layers}</strong>
              </label>
              <input
                id="layer-count"
                type="range"
                min="1"
                max="16"
                value={config.layers}
                disabled={disabled}
                onChange={(e) => patch("layers", +e.target.value)}
              />
            </div>
            <div className="field-group">
              <label htmlFor="detail">
                Detail <strong>{Math.round(config.detail * 100)}%</strong>
              </label>
              <input
                id="detail"
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={config.detail}
                disabled={disabled}
                onChange={(e) => patch("detail", +e.target.value)}
              />
            </div>
            <div className="two-fields">
              <div className="field-group">
                <label htmlFor="width">Width</label>
                <div className="unit-input">
                  <input
                    id="width"
                    type="number"
                    min="10"
                    max="2000"
                    value={config.width_mm}
                    disabled={disabled}
                    onChange={(e) => patch("width_mm", +e.target.value)}
                  />
                  <span>mm</span>
                </div>
              </div>
              <div className="field-group">
                <label htmlFor="spacing">Layer spacing</label>
                <div className="unit-input">
                  <input
                    id="spacing"
                    type="number"
                    min="0"
                    max="100"
                    step="0.5"
                    value={config.spacing_mm}
                    disabled={disabled}
                    onChange={(e) => patch("spacing_mm", +e.target.value)}
                  />
                  <span>mm</span>
                </div>
              </div>
            </div>
            <div className="two-fields">
              <div className="field-group">
                <label htmlFor="frame-width">Frame width (mm)</label>
                <input
                  id="frame-width"
                  type="number"
                  min="3"
                  step="1"
                  value={config.frame_mm}
                  disabled={disabled}
                  onChange={(e) => patch("frame_mm", +e.target.value)}
                />
              </div>
              <div className="field-group">
                <label htmlFor="sheet-height">Height (mm)</label>
                <input
                  id="sheet-height"
                  type="number"
                  min="10"
                  placeholder="Auto"
                  value={config.height_mm ?? ""}
                  disabled={disabled}
                  onChange={(e) =>
                    patch("height_mm", e.target.value ? +e.target.value : null)
                  }
                />
              </div>
            </div>
            <div className="field-group">
              <label htmlFor="art-fit">Image fit</label>
              <select
                id="art-fit"
                value={config.artwork_fit || "contain"}
                disabled={disabled}
                onChange={(e) =>
                  patch("artwork_fit", e.target.value as "contain" | "cover")
                }
              >
                <option value="contain">Contain</option>
                <option value="cover">Cover</option>
              </select>
            </div>
            <div className="two-fields">
              <div className="field-group">
                <label htmlFor="assembly">Assembly</label>
                <select
                  id="assembly"
                  disabled={disabled || workflow.family === "depth_scene"}
                  value={workflow.assembly}
                  onChange={(e) =>
                    patch("workflow", {
                      ...workflow,
                      assembly: e.target.value as "flat" | "spaced",
                    })
                  }
                >
                  <option value="flat">Flat stack</option>
                  <option value="spaced">Spaced layers</option>
                </select>
              </div>
              <div className="field-group">
                <label htmlFor="lighting">Lighting</label>
                <select
                  id="lighting"
                  disabled={disabled}
                  value={workflow.lighting}
                  onChange={(e) =>
                    patch("workflow", {
                      ...workflow,
                      lighting: e.target.value as "ambient" | "backlit",
                    })
                  }
                >
                  <option value="ambient">Ambient</option>
                  <option value="backlit">Backlit</option>
                </select>
              </div>
            </div>

            <details>
              <summary>
                Advanced settings <ChevronDown size={13} />
              </summary>
              <div className="field-group">
                <ScenePlanner
                  value={sceneText}
                  onChange={setSceneText}
                  disabled={disabled}
                />
                <label htmlFor="scene-plan">Scene JSON</label>
                <textarea
                  id="scene-plan"
                  rows={6}
                  value={sceneText}
                  disabled={disabled}
                  onChange={(e) => setSceneText(e.target.value)}
                  placeholder="Optional scene definition"
                />
              </div>
              <div className="field-group">
                <label htmlFor="palette">Custom palette</label>
                <input
                  id="palette"
                  value={paletteText}
                  disabled={disabled}
                  placeholder="#efe4cf, #36564b"
                  onChange={(e) => setPaletteText(e.target.value)}
                />
                <small>Comma-separated hex colors.</small>
              </div>
              <div className="field-group">
                <strong>AI assistance</strong>

                {(
                  [
                    ["segmentation", "Object segmentation"],
                    ["face_features", "Protect facial features"],
                    ["important_details", "Protect important details"],
                    ["depth", "Depth estimation"],
                  ] as const
                ).map(([key, label]) => (
                  <label key={key} className="checkbox-row">
                    <input
                      type="checkbox"
                      aria-label={label}
                      disabled={disabled}
                      checked={config.ai?.[key] ?? false}
                      onChange={(e) =>
                        patch("ai", {
                          segmentation: false,
                          depth: false,
                          face_features: false,
                          ...config.ai,
                          [key]: e.target.checked,
                        })
                      }
                    />{" "}
                    <span>{label}</span>
                    <small>
                      {modelStatus[key]?.status === "prepared"
                        ? "Available"
                        : "Fallback"}
                    </small>
                  </label>
                ))}
                {config.ai?.important_details && (
                  <>
                    <label>
                      Detail crops
                      <select
                        aria-label="AI detail crops"
                        disabled={disabled}
                        value={config.ai.detail_crops ?? 1}
                        onChange={(e) =>
                          patch("ai", {
                            ...config.ai!,
                            detail_crops: +e.target.value,
                          })
                        }
                      >
                        <option value={0}>0 · Full image</option>
                        <option value={1}>1 · Balanced</option>
                        <option value={2}>2 · Detailed</option>
                      </select>
                    </label>
                    <label>
                      Detail time budget (s)
                      <input
                        aria-label="AI detail budget"
                        type="number"
                        min={5}
                        max={180}
                        disabled={disabled}
                        value={config.ai.detail_seconds ?? 45}
                        onChange={(e) => {
                          const value = +e.target.value;
                          if (value >= 5 && value <= 180)
                            patch("ai", {
                              ...config.ai!,
                              detail_seconds: value,
                            });
                        }}
                      />
                    </label>
                  </>
                )}
                {config.ai?.segmentation && (
                  <label>
                    Subject bounds (0–1)
                    <input
                      aria-label="AI subject bounds"
                      placeholder="Automatic"
                      key={JSON.stringify(config.ai.subject_box ?? null)}
                      defaultValue={config.ai.subject_box?.join(", ") ?? ""}
                      disabled={disabled}
                      onBlur={(e) => {
                        const text = e.target.value.trim();
                        const values = text.split(",").map(Number);
                        if (
                          !text ||
                          (values.length === 4 &&
                            values.every(Number.isFinite) &&
                            values[0] >= 0 &&
                            values[1] >= 0 &&
                            values[2] <= 1 &&
                            values[3] <= 1 &&
                            values[0] < values[2] &&
                            values[1] < values[3])
                        ) {
                          patch("ai", {
                            ...config.ai!,
                            subject_box: text
                              ? (values as [number, number, number, number])
                              : null,
                          });
                          e.target.setCustomValidity("");
                        } else {
                          e.target.setCustomValidity(
                            "Enter valid x0,y0,x1,y1 coordinates within 0–1",
                          );
                          e.target.reportValidity();
                        }
                      }}
                    />
                  </label>
                )}

                {!!project?.metrics?.ai_diagnostics?.length && (
                  <details className="processing-details">
                    <summary>Processing details</summary>
                    {project?.metrics?.ai_diagnostics?.map((item, i) => (
                      <small key={i} role="status">
                        {item.component}:{" "}
                        {item.component === "detail_selection"
                          ? `${item.region_count || 0} regions · ${item.method === "semantic+structure" ? "Model + structure" : "Structure only"}`
                          : item.status === "applied"
                            ? "Applied"
                            : item.status === "no_detection"
                              ? "No detection"
                              : item.status === "not_applicable"
                                ? "Not applicable"
                                : "Fallback"}
                        {item.reason && (
                          <span title={item.reason}> · Unavailable</span>
                        )}
                      </small>
                    ))}
                  </details>
                )}
              </div>
              <div className="field-group">
                <label htmlFor="resolution">Resolution</label>
                <select
                  id="resolution"
                  value={config.max_side}
                  disabled={disabled}
                  onChange={(e) => patch("max_side", +e.target.value)}
                >
                  <option value="256">256 px · Fast</option>
                  <option value="384">384 px · Balanced</option>
                  <option value="768">768 px · Detailed</option>
                </select>
              </div>
              {config.mode === "lightbox" && (
                <>
                  <label htmlFor="depth-mode">Depth assignment</label>
                  <select
                    id="depth-mode"
                    value={depthMode}
                    onChange={(e) => setDepthMode(e.target.value)}
                  >
                    <option value="heuristic">Automatic</option>
                    <option value="manual">Manual</option>
                  </select>
                  {depthMode === "manual" && (
                    <div className="depth-assignments">
                      {project?.regions?.map((region) => (
                        <label key={region.id}>
                          Region {region.id + 1}
                          <select
                            aria-label={`Region depth ${region.id + 1}`}
                            value={depths[region.id] ?? 0}
                            onChange={(e) =>
                              setDepths({
                                ...depths,
                                [region.id]: +e.target.value,
                              })
                            }
                          >
                            {Array.from({ length: config.layers }, (_, i) => (
                              <option key={i} value={i}>
                                Layer {i + 1}
                              </option>
                            ))}
                          </select>
                        </label>
                      ))}
                    </div>
                  )}
                </>
              )}
            </details>
            <div className="field-group">
              <label htmlFor="display-frame">Display frame</label>
              <select
                id="display-frame"
                value={frameKind}
                onChange={(e) => setFrameKind(e.target.value as FrameKind)}
              >
                <option value="auto">Auto</option>
                <option value="walnut">Walnut</option>
                <option value="oak">Oak</option>
                <option value="white">White</option>
                <option value="none">None</option>
              </select>
            </div>
            <div className="field-group">
              <label htmlFor="time-limit">Time limit (s)</label>
              <input
                id="time-limit"
                title="30–1800 seconds; checked between processing stages."
                type="number"
                min="30"
                max="1800"
                step="30"
                value={timeLimit}
                disabled={disabled}
                onChange={(e) => {
                  setTimeLimit(Number(e.target.value));
                }}
                onBlur={() =>
                  setTimeLimit((value) =>
                    Math.max(30, Math.min(1800, Math.round(value || 180))),
                  )
                }
              />
            </div>
            <div className="generation-actions">
              {dirty && <p className="small-note">Unsaved settings</p>}
              <button
                className="button primary generate"
                disabled={disabled || !project}
                onClick={() =>
                  run(() => generate(project!), "Generating design")
                }
              >
                {busy === "Generating design" ? (
                  <LoaderCircle size={16} className="spin" />
                ) : (
                  <Sparkles size={16} />
                )}{" "}
                {layers.length ? "Regenerate" : "Generate design"}
              </button>
            </div>
          </aside>

          <section className="canvas-panel panel">
            {layers.length > 0 &&
              project?.report_current &&
              status === "FAIL" && (
                <div className="fabrication-warning" role="note">
                  <strong>DRAFT · NOT CUT-READY</strong>
                  <span>
                    Resolve disconnected or thin areas before cutting.
                  </span>
                  <button
                    className="button primary"
                    disabled={disabled}
                    onClick={() =>
                      run(
                        () => generate(project!),
                        "Preparing cut-ready design",
                      )
                    }
                  >
                    Make cut-ready
                  </button>
                </div>
              )}
            <div className="canvas-toolbar">
              <div className="view-tabs">
                <button
                  aria-label="Flat view"
                  className={view === "flat" ? "active" : ""}
                  onClick={() => setView("flat")}
                >
                  <Layers size={14} />
                  <span>Flat</span>
                </button>
                <button
                  aria-label="Assembly view"
                  className={view === "assembled" ? "active" : ""}
                  onClick={() => setView("assembled")}
                >
                  <Box size={14} />
                  <span>Assembled</span>
                </button>
                <button
                  aria-label="Exploded view"
                  className={view === "exploded" ? "active" : ""}
                  onClick={() => setView("exploded")}
                >
                  <Layers size={14} />
                  <span>Exploded</span>
                </button>
                {(config.mode === "lightbox" ||
                  workflow.lighting === "backlit") && (
                  <button
                    aria-label="Light preview"
                    className={view === "lightbox" ? "active" : ""}
                    onClick={() => setView("lightbox")}
                  >
                    <Lightbulb size={14} />
                    <span>Light</span>
                  </button>
                )}
                <button
                  aria-label="Original image"
                  className={view === "original" ? "active" : ""}
                  onClick={() => setView("original")}
                >
                  <ImagePlus size={14} />
                  <span>Original</span>
                </button>
              </div>
              <span className="view-indicator">
                <i /> LIVE PREVIEW
              </span>
            </div>
            <div
              className={`canvas ${view === "lightbox" ? "dark-canvas" : ""}`}
              onWheel={(e) => {
                if (view === "flat" || view === "original")
                  setZoom((z) =>
                    Math.max(0.3, Math.min(3, z - e.deltaY * 0.001)),
                  );
              }}
              onPointerDown={(e) => {
                if ((view === "flat" || view === "original") && layers.length) {
                  dragging.current = {
                    x: e.clientX,
                    y: e.clientY,
                    px: pan.x,
                    py: pan.y,
                  };
                  e.currentTarget.setPointerCapture(e.pointerId);
                }
              }}
              onPointerMove={(e) => {
                const d = dragging.current;
                if (d)
                  setPan({
                    x: d.px + e.clientX - d.x,
                    y: d.py + e.clientY - d.y,
                  });
              }}
              onPointerUp={() => {
                dragging.current = null;
              }}
            >
              {layers.length && project ? (
                view === "original" ? (
                  <img
                    className="source-preview"
                    style={{
                      transform: `translate(${pan.x}px,${pan.y}px) scale(${zoom})`,
                    }}
                    src={`/projects/${project.id}/source`}
                    alt="Original image"
                  />
                ) : view === "flat" ? (
                  <div
                    className={`paper ${frameKind !== "none" && !selected && visible.size > 1 ? "display-surround" : ""}`}
                    style={
                      {
                        ...Object.fromEntries(
                          Object.entries(framePalette(project, frameKind))
                            .filter(([k]) => k !== "material")
                            .map(([key, value]) => [`--display-${key}`, value]),
                        ),
                        aspectRatio: `${project.width_mm}/${project.height_mm}`,
                        transform: `translate(${pan.x}px,${pan.y}px) scale(${zoom})`,
                      } as CSSProperties
                    }
                  >
                    <FlatPreview
                      project={project}
                      visible={visible}
                      selected={selected}
                      focusedIssue={focusedIssue}
                      shadows={showShadows}
                    />
                  </div>
                ) : (
                  <Suspense
                    fallback={
                      <div className="canvas-empty">Loading 3D preview…</div>
                    }
                  >
                    <Scene
                      frameKind={frameKind}
                      project={project}
                      view={view}
                      visible={visible}
                      intensity={intensity}
                      lightColor={lightColor}
                      shadows={showShadows}
                      lightMode={lightMode}
                      transmittance={transmittance}
                      sourceDistance={sourceDistance}
                      diffusion={lightDiffusion}
                      photoOverlay={photoOverlayReady && photoOverlay}
                      photoStrength={photoStrength}
                    />
                  </Suspense>
                )
              ) : (
                <div className="empty-preview">
                  <div className="welcome-art">
                    <div className="art-sheet sheet-back" />
                    <div className="art-sheet sheet-mid" />
                    <div className="art-sheet sheet-front">
                      <svg viewBox="0 0 340 270">
                        <rect width="340" height="270" fill="#eee3cd" />
                        <circle cx="238" cy="76" r="36" fill="#d5a16c" />
                        <path
                          d="M0 180 75 80 155 180 225 112 340 185V270H0Z"
                          fill="#a5a99d"
                        />
                        <path
                          d="M0 215 105 149 217 210 295 154 340 175V270H0Z"
                          fill="#71877c"
                        />
                        <path
                          d="M0 244 88 209 180 245 269 209 340 231V270H0Z"
                          fill="#36564b"
                        />
                      </svg>
                    </div>
                  </div>
                  <span className="sample-caption">
                    MOUNTAIN STUDY <i /> Sample artwork
                  </span>
                  <div className="canvas-prompt">
                    <span>Your next paper artwork</span>
                    <small>Upload an image to get started.</small>
                  </div>
                </div>
              )}
              <span className="corner-label">
                {layers.length
                  ? `${project?.width_mm.toFixed(0)} × ${project?.height_mm.toFixed(0)} mm`
                  : "PREVIEW"}
              </span>
              {(view === "flat" || view === "original") && (
                <div className="zoom-controls">
                  <button
                    aria-label="Zoom out"
                    onClick={() => setZoom((z) => Math.max(0.3, z - 0.1))}
                  >
                    <ZoomOut size={15} />
                  </button>
                  <span>{Math.round(zoom * 100)}%</span>
                  <button
                    aria-label="Zoom in"
                    onClick={() => setZoom((z) => Math.min(3, z + 0.1))}
                  >
                    <ZoomIn size={15} />
                  </button>
                  <button
                    aria-label="Reset view"
                    onClick={() => {
                      setZoom(1);
                      setPan({ x: 0, y: 0 });
                    }}
                  >
                    <RotateCcw size={13} />
                  </button>
                </div>
              )}
            </div>
            {view === "lightbox" && (
              <div className="light-controls">
                {lightPreviewReady === false && (
                  <small role="alert">
                    Restart the backend to update light preview.
                  </small>
                )}
                <Lightbulb size={16} />
                <label>
                  <input
                    type="checkbox"
                    aria-label="Original photo insert"
                    disabled={!photoOverlayReady}
                    checked={photoOverlay}
                    onChange={(e) => setPhotoOverlay(e.target.checked)}
                  />
                  Original photo insert
                </label>
                {!photoOverlayReady && (
                  <small>Restart the backend to enable photo insert.</small>
                )}
                {photoOverlay && photoOverlayReady && (
                  <>
                    <label>
                      Photo strength
                      <input
                        aria-label="Photo strength"
                        type="range"
                        min="0"
                        max="1"
                        step=".05"
                        value={photoStrength}
                        onChange={(e) => setPhotoStrength(+e.target.value)}
                      />
                      <span>{Math.round(photoStrength * 100)}%</span>
                    </label>
                  </>
                )}
                <label>
                  Source
                  <select
                    aria-label="Light source"
                    value={lightMode}
                    onChange={(e) => setLightMode(e.target.value)}
                  >
                    <option value="source">Image color zones</option>
                    <option value="uniform">Uniform light</option>
                  </select>
                </label>
                <label>
                  Light intensity
                  <input
                    aria-label="Light intensity"
                    type="range"
                    min="-2"
                    max="12"
                    step=".25"
                    value={Math.log2(intensity)}
                    onChange={(e) => setIntensity(2 ** +e.target.value)}
                  />
                  <span>
                    {intensity < 10
                      ? intensity.toFixed(2)
                      : Math.round(intensity)}
                    ×
                  </span>
                </label>
                {lightMode === "uniform" && (
                  <label>
                    Light color
                    <input
                      aria-label="Light color"
                      type="color"
                      value={lightColor}
                      onChange={(e) => setLightColor(e.target.value)}
                    />
                  </label>
                )}
                {
                  <label>
                    {project?.config.depth_paper === "multicolor"
                      ? "Paper transmission"
                      : "Paper transmission"}
                    <input
                      aria-label="Paper transmission"
                      type="range"
                      min=".01"
                      max="1"
                      step=".01"
                      value={transmittance}
                      onChange={(e) => setTransmittance(+e.target.value)}
                    />
                    <span>
                      {Math.round(transmittance * 100)}% transmission
                      {project?.config.depth_paper === "multicolor"
                        ? " backing"
                        : ""}{" "}
                      · {Math.round((1 - transmittance) * 100)}% blocked
                    </span>
                  </label>
                }
              </div>
            )}
            {view === "lightbox" && project && layers.length > 0 && (
              <TransmissionPanel
                project={project}
                transmittance={transmittance}
                visible={visible}
              />
            )}
            {view === "lightbox" &&
              lightMode === "source" &&
              project &&
              layers.length > 0 && (
                <LightPalette
                  project={project}
                  distance={sourceDistance}
                  diffusion={lightDiffusion}
                  onDistance={setSourceDistance}
                  onDiffusion={setLightDiffusion}
                />
              )}
            {project && layers.length > 0 && view !== "original" && (
              <div className="preview-guides">
                <span>
                  {view === "flat" && (selected || visible.size === 1)
                    ? "Hatched: remove · Solid: keep"
                    : "Select a layer to inspect cutouts"}
                  {selected && view === "flat" ? ` · ${selected}` : ""}
                </span>
                <label>
                  <input
                    type="checkbox"
                    aria-label="Layer shadows"
                    checked={showShadows}
                    onChange={(e) => setShowShadows(e.target.checked)}
                  />{" "}
                  Shadows
                </label>
              </div>
            )}
            <div className="canvas-footer">
              <span>
                <span className="tiny-square" />
                {layers.length
                  ? `${layers.length} paper layers · ${config.mode === "lightbox" ? "Lightbox" : "Multicolor papercut"}`
                  : "Image to paper"}
              </span>
              <span>
                {view === "flat"
                  ? "Scroll to zoom · Drag to pan"
                  : "Drag to rotate · Scroll to zoom"}
              </span>
            </div>
          </section>

          <aside className="layers-panel panel">
            <div className="panel-title">
              <span className="step">02</span>
              <h2>Paper layers</h2>
              <span className="count">{layers.length}</span>
            </div>
            <div className="right-tabs">
              <button
                className={tab === "layers" ? "active" : ""}
                onClick={() => setTab("layers")}
              >
                Layers
              </button>
              <button
                className={tab === "issues" ? "active" : ""}
                onClick={() => setTab("issues")}
              >
                Checks {findings.length > 0 && <span>{findings.length}</span>}
              </button>
            </div>
            <div className="layer-list">
              {tab === "layers" ? (
                layers.length ? (
                  <>
                    <p className="list-direction">FRONT → BACK</p>
                    {layers.map((layer, index) => (
                      <div
                        key={layer.id}
                        data-testid="layer-row"
                        className={`layer-row ${selected === layer.id ? "chosen" : ""}`}
                      >
                        <button
                          className="layer-thumb"
                          onClick={() => {
                            setSelected(
                              selected === layer.id ? null : layer.id,
                            );
                            setVisible((prev) => new Set([...prev, layer.id]));
                            setView("flat");
                          }}
                          aria-label={`Isolate ${layer.id}`}
                        >
                          <svg
                            viewBox={`0 0 ${project!.width_mm} ${project!.height_mm}`}
                          >
                            <path
                              d={layer.path}
                              fill={layer.color}
                              fillRule="evenodd"
                            />
                          </svg>
                        </button>
                        <div className="layer-label">
                          <strong>Layer {index + 1}</strong>
                          <span>{layer.color.toUpperCase()}</span>
                          <input
                            type="color"
                            aria-label={`Layer color ${layer.id}`}
                            value={layer.color}
                            disabled={disabled}
                            onChange={(e) =>
                              run(
                                () =>
                                  edit("set_color", {
                                    layer_id: layer.id,
                                    color: e.target.value,
                                  }),
                                "Updating color",
                              )
                            }
                          />
                        </div>
                        <div className="layer-actions">
                          <button
                            aria-label={`Toggle ${layer.id}`}
                            onClick={() =>
                              setVisible((prev) => {
                                const next = new Set(prev);
                                if (next.has(layer.id)) next.delete(layer.id);
                                else next.add(layer.id);
                                return next;
                              })
                            }
                          >
                            {visible.has(layer.id) ? (
                              <Eye size={14} />
                            ) : (
                              <EyeOff size={14} />
                            )}
                          </button>
                          <button
                            aria-label={`Move ${layer.id} forward`}
                            disabled={disabled || index === 0}
                            onClick={() => {
                              const ids = layers.map((l) => l.id);
                              [ids[index - 1], ids[index]] = [
                                ids[index],
                                ids[index - 1],
                              ];
                              run(
                                () => edit("reorder", { layer_ids: ids }),
                                "Reordering",
                              );
                            }}
                          >
                            <ArrowUp size={12} />
                          </button>
                          <button
                            aria-label={`Move ${layer.id} backward`}
                            disabled={disabled || index === layers.length - 1}
                            onClick={() => {
                              const ids = layers.map((l) => l.id);
                              [ids[index], ids[index + 1]] = [
                                ids[index + 1],
                                ids[index],
                              ];
                              run(
                                () => edit("reorder", { layer_ids: ids }),
                                "Reordering",
                              );
                            }}
                          >
                            <ArrowDown size={12} />
                          </button>
                        </div>
                      </div>
                    ))}
                    <button
                      className="clear-selection"
                      onClick={() => {
                        setSelected(null);
                        setVisible(new Set(layers.map((l) => l.id)));
                      }}
                    >
                      Show all layers
                    </button>
                  </>
                ) : (
                  <div className="empty-layers">
                    <Layers size={30} strokeWidth={1} />
                    <strong>Your layers appear here</strong>
                    <p>Generate a design to get started.</p>
                    <span>01 — 02 — 03</span>
                  </div>
                )
              ) : (
                <div className="issue-list">
                  {!project?.report_current ? (
                    <p>Validate this revision to see issues.</p>
                  ) : !findings.length ? (
                    <p className="all-clear">
                      <ShieldCheck size={25} /> No geometry issues found.
                    </p>
                  ) : (
                    findings.map((issue, index) => (
                      <button
                        key={index}
                        onClick={() => {
                          setFocusedIssue(issue);
                          setSelected(issue.layer_id);
                          setView("flat");
                        }}
                      >
                        <strong>{issue.code}</strong>
                        <small>
                          {issue.layer_id} ·{" "}
                          {issue.location_mm
                            .map((v) => v.toFixed(1))
                            .join(", ")}{" "}
                          mm
                        </small>
                        <span>{issue.message}</span>
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
            <div className="fabrication-box">
              <div>
                <ShieldCheck size={17} />
                <strong>Fabrication</strong>
                <span
                  className={`status-dot ${currentPass ? "green" : layers.length ? "amber" : ""}`}
                />
              </div>
              <p className={`status-text ${currentPass ? "pass" : ""}`}>
                {status}
              </p>
              <small>
                {layers.length
                  ? "Geometry checked · Physical cut untested"
                  : "Generate a design to validate."}
              </small>
              {project?.report?.image_fidelity && layers.length > 0 && (
                <small aria-label="Image quality">
                  Fidelity:{" "}
                  {project.report.image_fidelity.status === "PASS"
                    ? "Checks passed"
                    : project.report.image_fidelity.status === "WARNING"
                      ? "Detail review needed"
                      : project.report.image_fidelity.status === "FAIL"
                        ? "Needs work"
                        : "Not evaluated"}
                  {project.report.image_fidelity.construction ===
                    "color-occlusion relief" && " · Color relief"}
                  {""}
                </small>
              )}
              {!!project?.report?.image_fidelity?.automatic_detail_regions
                ?.length && (
                <details className="field-group">
                  <summary>
                    Detail checks (
                    {
                      project.report.image_fidelity.automatic_detail_regions
                        .length
                    }{" "}
                    regions)
                  </summary>
                  {project.report.image_fidelity.automatic_detail_regions.map(
                    (r) => (
                      <small key={r.index}>
                        Region {r.index} · {r.label}:{" "}
                        {r.status === "PASS" ? "Passed" : "Review"} · Match{" "}
                        {Math.round(r.color_agreement * 100)}% · Contrast{" "}
                        {Math.round(r.contrast_ratio * 100)}%
                      </small>
                    ),
                  )}
                </details>
              )}
              <button
                className="button secondary"
                disabled={disabled || !layers.length}
                onClick={() => run(validate, "Validating")}
              >
                <ScanLine size={14} /> Validate design
              </button>
              <button
                className="text-button"
                disabled={disabled || !layers.length}
                onClick={() => run(() => edit("repair"), "Repairing geometry")}
              >
                Repair geometry
              </button>
              <details>
                <summary>Material profile</summary>
                {[
                  "min_feature_width_mm",
                  "min_bridge_width_mm",
                  "min_hole_size_mm",
                  "thickness_mm",
                ].map((key) => (
                  <label className="profile-field" key={key}>
                    {
                      (
                        {
                          min_feature_width_mm: "Min. feature width",
                          min_bridge_width_mm: "Min. bridge width",
                          min_hole_size_mm: "Min. hole size",
                          thickness_mm: "Paper thickness",
                        } as Record<string, string>
                      )[key]
                    }
                    <input
                      aria-label={key}
                      type="number"
                      step="0.1"
                      min="0.1"
                      max="20"
                      disabled={disabled || !layers.length}
                      value={project?.profile?.[key] ?? ""}
                      onChange={(e) =>
                        run(
                          () => edit("set_profile", { [key]: +e.target.value }),
                          "Updating material",
                        )
                      }
                    />
                    <span>mm</span>
                  </label>
                ))}
              </details>
              {project?.repairs?.some(
                (record) => record.operation === "bridge",
              ) && (
                <details>
                  <summary>Supports</summary>
                  {project.repairs
                    .filter((record) => record.operation === "bridge")
                    .map((record) => (
                      <div className="bridge-control" key={record.index}>
                        <label>
                          <input
                            type="checkbox"
                            checked={record.enabled !== false}
                            disabled={disabled}
                            onChange={(e) =>
                              run(
                                () =>
                                  edit("set_bridge", {
                                    repair_index: record.index,
                                    enabled: e.target.checked,
                                  }),
                                "Updating support",
                              )
                            }
                          />{" "}
                          {record.layer_id} · support {record.index + 1}
                        </label>
                        <label>
                          Width{" "}
                          <input
                            aria-label={`Support width ${record.index + 1}`}
                            type="number"
                            min={Number(project.profile.min_bridge_width_mm)}
                            max="20"
                            step="0.1"
                            defaultValue={
                              record.adjusted_width_mm ??
                              Math.max(
                                Number(project.profile.min_bridge_width_mm),
                                Number(project.profile.min_feature_width_mm) *
                                  1.5,
                              )
                            }
                            disabled={disabled}
                            onBlur={(e) => {
                              if (e.target.value)
                                run(
                                  () =>
                                    edit("set_bridge", {
                                      repair_index: record.index,
                                      enabled: true,
                                      width_mm: +e.target.value,
                                    }),
                                  "Updating support",
                                );
                            }}
                          />{" "}
                          mm
                        </label>
                      </div>
                    ))}
                </details>
              )}
            </div>
          </aside>
        </div>

        <section className="export-bar">
          {project?.legacy_requires_conversion && (
            <button
              disabled={disabled}
              onClick={() =>
                run(() => edit("convert_legacy"), "Converting project")
              }
            >
              Convert legacy project
            </button>
          )}
          <div className="export-icon">
            <Download size={20} />
          </div>
          <div>
            <h3>Export your design</h3>
            <p>Print at 100% scale. Verify the ruler before cutting.</p>
            {project?.material_accounting && (
              <small>
                {project.material_accounting.cut_sheet_count} cut sheets ·{" "}
                {project.material_accounting.colors_used} colors ·{" "}
                {project.material_accounting.uncut_backing_count} backing ·{" "}
                {project.material_accounting.diffuser_count} diffusers ·{" "}
                {project.material_accounting.colored_source_filter_count
                  ? `${project.material_accounting.colored_source_filter_count} color filters · `
                  : ""}
                {project.material_accounting.spacer_count} spacer sets
              </small>
            )}
          </div>
          <div className="export-options">
            <select
              aria-label="Paper size"
              value={printPaper}
              onChange={(e) => setPrintPaper(e.target.value)}
            >
              <option>A4</option>
              <option>A3</option>
            </select>
            <select
              aria-label="Page orientation"
              value={printOrientation}
              onChange={(e) => setPrintOrientation(e.target.value)}
            >
              <option value="auto">Auto orientation</option>
              <option value="portrait">Portrait</option>
              <option value="landscape">Landscape</option>
            </select>
            <select
              aria-label="Print side"
              value={printSide}
              onChange={(e) => setPrintSide(e.target.value)}
            >
              <option value="front">Front</option>
              <option value="back">Back · mirrored</option>
            </select>

            <select
              aria-label="More export formats"
              defaultValue=""
              disabled={disabled || !layers.length}
              onChange={(e) => {
                if (e.target.value)
                  run(() => download(e.target.value), "Preparing download");
                e.target.value = "";
              }}
            >
              <option value="" disabled>
                More formats…
              </option>
              <option value="light_filter">Light filter PDF</option>
              <option value="pdf">All layers PDF · 1:1</option>
              <option value="cut_svg">Selected layer · Cut SVG</option>
              <option value="svg">Selected layer · Material SVG</option>
              <option value="dxf">Selected layer · DXF</option>
              <option value="png">PNG preview</option>
              <option value="report">Validation report</option>
            </select>
            <button
              className="button secondary"
              disabled={disabled || !layers.length}
              onClick={() => run(() => download("zip"), "Preparing ZIP")}
            >
              <Download size={15} /> Download draft
            </button>
            <button
              className="button primary"
              disabled={disabled || !currentPass}
              onClick={() =>
                run(() => download("zip", "cut_ready"), "Preparing cut files")
              }
            >
              <ShieldCheck size={16} /> Download cut-ready
            </button>
          </div>
        </section>
        <footer>
          <span>Designed digitally. Made by hand.</span>
          <span>
            Geometry-first <i /> Local-first <i /> PaperCraft AI
          </span>
        </footer>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
