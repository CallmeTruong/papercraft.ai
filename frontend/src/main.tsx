import { lazy, Suspense, useEffect, useRef, useState } from "react";
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
import "@fontsource/manrope/vietnamese-600.css";
import "@fontsource/manrope/latin-800.css";
import "@fontsource/manrope/vietnamese-800.css";
import type { Config, Project, Finding, Workflow } from "./types";
import "./style.css";
import { ScenePlanner } from "./ScenePlanner";
import { FlatPreview } from "./FlatPreview";

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
  workflow: {
    family: "multicolor",
    style: "colorblock",
    assembly: "flat",
    lighting: "ambient",
  },
};

async function request(path: string, options: RequestInit = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      data.error?.message || data.detail || "Không thể thực hiện thao tác.",
    );
  return data;
}
const json = (value: unknown) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});

function App() {
  const [project, setProject] = useState<Project | null>(null);
  const [config, setConfig] = useState<Config>(defaults);
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
  const [transmittance, setTransmittance] = useState(0.55);
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
      setLightMode("source");
      setTransmittance(data.light_plan.paper_transmittance_assumption);
    }
    setSceneText(
      data.scene_plan ? JSON.stringify(data.scene_plan, null, 2) : "",
    );
    setConfig(data.config);
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
    const id = localStorage.getItem("papercraft-project");
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
      const [stage, layer] = String(job.stage || "starting").split(":");
      const stages: Record<string, string> = {
        queued: "Chờ xử lý",
        starting: "Bắt đầu",
        image_abstraction: "Phân tích ảnh và gộp vùng",
        construct_layers: "Dựng các lớp giấy",
        vectorize: "Tạo đường cắt",
        fabricate: "Gia cố và nối giấy về khung",
        validate: "Kiểm tra liền khung và độ rộng",
        repair: "Tìm cầu nối và sửa vật liệu",
        save: "Lưu thiết kế",
        complete: "Hoàn tất",
      };
      setJobDetail(
        `${stages[stage] || stage}${layer ? ` · ${layer}` : ""} · ${Math.round((job.progress || 0) * 100)}% · ${Math.floor(job.elapsed_seconds || 0)} giây`,
      );
      if (job.state === "succeeded") return job;
      if (job.state === "failed") throw new Error(job.error.message);
      if (job.state === "cancelled")
        throw new Error("Đã hủy thao tác. Thiết kế trước được giữ lại.");
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }

  async function generate(data: Project) {
    const settings = {
      ...config,
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
        source_lighting: lightMode === "source",
        paper_transmittance: transmittance,
        scene_plan: sceneText.trim() ? JSON.parse(sceneText) : null,
        depths:
          config.mode === "lightbox" && depthMode === "manual" ? depths : null,
      }),
    );
    await poll(job.job_id);
    apply(await request(`/projects/${data.id}`));
    setNotice("Thiết kế đã tạo · Đã qua kiểm tra chế tác từng lớp");
    setSelected(null);
    setFocusedIssue(null);
  }

  async function upload(file: File) {
    const form = new FormData();
    form.append("image", file);
    form.append("config", JSON.stringify(config));
    const data = await request("/projects", { method: "POST", body: form });
    apply(data);
    await generate(data);
  }

  async function demo() {
    const canvas = document.createElement("canvas");
    canvas.width = 360;
    canvas.height = 280;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#eee3cd";
    ctx.fillRect(0, 0, 360, 280);
    ctx.fillStyle = "#dcac77";
    ctx.beginPath();
    ctx.arc(238, 75, 36, 0, Math.PI * 2);
    ctx.fill();
    const mountain = (color: string, points: number[][]) => {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(0, 280);
      points.forEach(([x, y]) => ctx.lineTo(x, y));
      ctx.lineTo(360, 280);
      ctx.closePath();
      ctx.fill();
    };
    mountain("#a0a399", [
      [0, 170],
      [72, 78],
      [150, 174],
      [220, 108],
      [360, 182],
    ]);
    mountain("#6e857c", [
      [0, 208],
      [110, 146],
      [218, 210],
      [300, 145],
      [360, 170],
    ]);
    mountain("#344f49", [
      [0, 238],
      [90, 207],
      [190, 248],
      [270, 208],
      [360, 228],
    ]);
    const blob = await new Promise<Blob>((resolve) =>
      canvas.toBlob((b) => resolve(b!), "image/png"),
    );
    await upload(new File([blob], "mountain-study.png", { type: "image/png" }));
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
    setNotice("Đã cập nhật báo cáo kiểm tra");
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
    setNotice("File đã sẵn sàng tải xuống");
  }

  const layers = project?.layers || [];
  const findings =
    project?.report?.layers.flatMap((report) => report.findings) || [];
  const status = !layers.length
    ? "Chưa có thiết kế"
    : !project?.report_current
      ? "Cần kiểm tra lại"
      : project?.report?.status || "Chưa kiểm tra";
  const currentPass = project?.report_current && status === "PASS";
  const dirty =
    project && JSON.stringify(project.config) !== JSON.stringify(config);
  const disabled = !!busy;

  return (
    <div className="app">
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
          Xưởng thiết kế <ChevronDown size={13} />
        </span>
        <span className="cpu-chip">
          <i /> CPU · Không cần AI
        </span>
        <div className="header-actions">
          <button
            className="icon-button"
            aria-label="Hoàn tác"
            disabled={disabled || !project?.can_undo}
            onClick={() => run(() => edit("undo"), "Đang hoàn tác")}
          >
            <Undo2 size={17} />
          </button>
          <button
            className="icon-button"
            aria-label="Làm lại"
            disabled={disabled || !project?.can_redo}
            onClick={() => run(() => edit("redo"), "Đang làm lại")}
          >
            <Redo2 size={17} />
          </button>
          <button
            className="button secondary compact"
            disabled={disabled}
            onClick={() => projectInput.current?.click()}
          >
            <FileUp size={15} />
            <span>Mở project</span>
          </button>
          <button
            className="button dark compact"
            disabled={disabled || !layers.length}
            onClick={() => run(() => download("project"), "Đang lưu project")}
          >
            <Download size={15} />
            <span>Lưu project</span>
          </button>
        </div>
      </header>

      <main>
        <section className="intro">
          <div>
            <span className="eyebrow">
              <span /> PAPER, MEET POSSIBILITY
            </span>
            <h1>Từ một bức ảnh, thành từng lớp giấy.</h1>
            <p>Đơn giản hóa. Xếp lớp. Tạo nên điều bạn có thể chạm vào.</p>
          </div>
          <div className="workflow">
            <span className={layers.length ? "done" : "active"}>
              01 <small>Ảnh & cấu hình</small>
            </span>
            <b>—</b>
            <span className={layers.length ? "active" : ""}>
              02 <small>Thiết kế lớp</small>
            </span>
            <b>—</b>
            <span>
              03 <small>Cắt & lắp ráp</small>
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
            <span>
              {busy
                ? `${busy}${jobDetail ? ` — ${jobDetail}` : ""}`
                : error || notice}
            </span>
            {busy && jobId && (
              <button
                onClick={() =>
                  request(`/jobs/${currentJob.current}/cancel`, {
                    method: "POST",
                  })
                }
              >
                Hủy
              </button>
            )}
            {!busy && (
              <button
                aria-label="Đóng thông báo"
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
              <h2>Ảnh & cấu hình</h2>
            </div>
            <button
              className={`upload-zone ${project ? "has-image" : ""}`}
              disabled={disabled}
              onClick={() => fileInput.current?.click()}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                const file = event.dataTransfer.files[0];
                if (file && !busy) run(() => upload(file), "Đang tạo thiết kế");
              }}
            >
              {project ? (
                <>
                  <img
                    src={`/projects/${project.id}/source`}
                    alt="Ảnh đầu vào"
                  />
                  <span>
                    <ImagePlus size={14} /> Thay ảnh khác
                  </span>
                </>
              ) : (
                <>
                  <span className="upload-icon">
                    <ImagePlus size={25} />
                  </span>
                  <strong>Thả ảnh của bạn vào đây</strong>
                  <small>hoặc nhấn để chọn ảnh</small>
                  <em>PNG, JPG, WEBP · tối đa 30 MB</em>
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
                if (file) run(() => upload(file), "Đang tạo thiết kế");
                e.target.value = "";
              }}
            />
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
                    "Đang mở project",
                  );
                e.target.value = "";
              }}
            />
            <button
              className="sample-link"
              disabled={disabled}
              onClick={() => run(demo, "Đang tạo thiết kế")}
            >
              <Sparkles size={13} /> Dùng ảnh mẫu
            </button>
            <button
              className="sample-link"
              disabled={disabled}
              onClick={() =>
                run(async () => {
                  apply(
                    await request(
                      `/references/forest-deer?mode=${config.mode}`,
                      { method: "POST" },
                    ),
                  );
                  setNotice(
                    "Mẫu rừng–hươu: 5 lớp liền khung, đã kiểm tra hình học.",
                  );
                }, "Đang tải mẫu cắt")
              }
            >
              <Layers size={13} /> Mẫu rừng–hươu 5 lớp
            </button>

            <div className="field-group">
              <label>Loại tác phẩm</label>
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
              <label htmlFor="art-style">Phong cách</label>
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
                    <option value="bold">Bold · mảng lớn</option>
                    <option value="detailed">Detailed · chi tiết</option>
                  </>
                ) : workflow.family === "multicolor" ? (
                  <>
                    <option value="colorblock">Colorblock</option>
                    <option value="splash_portrait">
                      Splash Portrait · ScenePlan thủ công
                    </option>
                  </>
                ) : (
                  <>
                    <option value="landscape">Landscape</option>
                    <option value="portal">Portal · ScenePlan thủ công</option>
                  </>
                )}
              </select>
              <small>
                {workflow.family === "depth_scene"
                  ? "Gán depth thủ công hoặc dùng gợi ý bố cục; chưa có mô hình suy luận depth."
                  : workflow.style === "splash_portrait"
                    ? "Cần ScenePlan từ thiết kế Splash đã chuẩn bị. Chưa có mô hình tạo phong cách."
                    : "Hình học cắt và chất lượng hình ảnh được đánh giá riêng."}
              </small>
            </div>
            <div className="field-group">
              <label htmlFor="colors">
                Số màu giấy <strong>{config.colors}</strong>
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
                <span>Đơn giản</span>
                <span>Phong phú</span>
              </div>
            </div>
            <div className="field-group">
              <label htmlFor="layer-count">
                Số lớp tối đa <strong>{config.layers}</strong>
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
                Mức chi tiết <strong>{Math.round(config.detail * 100)}%</strong>
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
                <label htmlFor="width">Chiều rộng</label>
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
                <label htmlFor="spacing">Cách lớp</label>
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
                <label htmlFor="frame-width">Viền khung (mm)</label>
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
                <label htmlFor="sheet-height">Chiều cao (mm)</label>
                <input
                  id="sheet-height"
                  type="number"
                  min="10"
                  placeholder="Theo ảnh"
                  value={config.height_mm ?? ""}
                  disabled={disabled}
                  onChange={(e) =>
                    patch("height_mm", e.target.value ? +e.target.value : null)
                  }
                />
              </div>
            </div>
            <div className="field-group">
              <label htmlFor="art-fit">Đặt ảnh trong khung</label>
              <select
                id="art-fit"
                value={config.artwork_fit || "contain"}
                disabled={disabled}
                onChange={(e) =>
                  patch("artwork_fit", e.target.value as "contain" | "cover")
                }
              >
                <option value="contain">Đặt vừa · giữ đầy đủ ảnh</option>
                <option value="cover">Cắt phủ · lấp đầy cửa sổ</option>
              </select>
            </div>
            <div className="two-fields">
              <div className="field-group">
                <label htmlFor="assembly">Cách xếp lớp</label>
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
                  <option value="flat">Xếp phẳng</option>
                  <option value="spaced">Shadowbox · cách lớp</option>
                </select>
              </div>
              <div className="field-group">
                <label htmlFor="lighting">Chiếu sáng</label>
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
                  <option value="ambient">Ánh sáng môi trường</option>
                  <option value="backlit">Lightbox · đèn phía sau</option>
                </select>
              </div>
            </div>
            <small>
              Mọi tấm giấy phải liền khung. Không có ngoại lệ dán các mảnh rời.
            </small>
            <details>
              <summary>
                Tùy chỉnh nâng cao <ChevronDown size={13} />
              </summary>
              <div className="field-group">
                <small>
                  Tự động gia cố và nối về khung trước khi hoàn tất; chỉ nhận
                  bản qua kiểm tra, thay đổi vật liệu tối đa 5% mỗi lớp.
                </small>
                <ScenePlanner
                  value={sceneText}
                  onChange={setSceneText}
                  disabled={disabled}
                />
                <label htmlFor="scene-plan">
                  ScenePlan · vật thể và vùng khoét (JSON)
                </label>
                <textarea
                  id="scene-plan"
                  rows={6}
                  value={sceneText}
                  disabled={disabled}
                  onChange={(e) => setSceneText(e.target.value)}
                  placeholder="Để trống để phân tích ảnh; có thể sửa ScenePlan của mẫu rừng–hươu."
                />
              </div>
              <div className="field-group">
                <label htmlFor="palette">Palette tùy chọn</label>
                <input
                  id="palette"
                  value={paletteText}
                  disabled={disabled}
                  placeholder="#efe4cf, #36564b"
                  onChange={(e) => setPaletteText(e.target.value)}
                />
                <small>Các mã màu cách nhau bằng dấu phẩy.</small>
              </div>
              <div className="field-group">
                <label htmlFor="resolution">Độ phân giải xử lý</label>
                <select
                  id="resolution"
                  value={config.max_side}
                  disabled={disabled}
                  onChange={(e) => patch("max_side", +e.target.value)}
                >
                  <option value="256">256 px · Nhanh</option>
                  <option value="384">384 px · Cân bằng</option>
                  <option value="768">768 px · Chi tiết</option>
                </select>
              </div>
              {config.mode === "lightbox" && (
                <>
                  <label htmlFor="depth-mode">Phân lớp chiều sâu</label>
                  <select
                    id="depth-mode"
                    value={depthMode}
                    onChange={(e) => setDepthMode(e.target.value)}
                  >
                    <option value="heuristic">Gợi ý theo bố cục dọc</option>
                    <option value="manual">Gán thủ công theo vùng</option>
                  </select>
                  {depthMode === "manual" && (
                    <div className="depth-assignments">
                      {project?.regions?.map((region) => (
                        <label key={region.id}>
                          Vùng {region.id + 1}
                          <select
                            aria-label={`Chiều sâu vùng ${region.id + 1}`}
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
                                Lớp {i + 1}
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
            {dirty && (
              <p className="small-note">Cấu hình đã đổi. Tạo lại để áp dụng.</p>
            )}
            <button
              className="button primary generate"
              disabled={disabled || !project}
              onClick={() => run(() => generate(project!), "Đang tạo thiết kế")}
            >
              {busy === "Đang tạo thiết kế" ? (
                <LoaderCircle size={16} className="spin" />
              ) : (
                <Sparkles size={16} />
              )}{" "}
              {layers.length ? "Tạo lại thiết kế" : "Tạo thiết kế"}
            </button>
            <p className="local-note">
              <ShieldCheck size={12} /> Ảnh được xử lý trên máy của bạn
            </p>
          </aside>

          <section className="canvas-panel panel">
            {layers.length > 0 &&
              project?.report_current &&
              status === "FAIL" && (
                <div className="fabrication-warning" role="note">
                  <strong>BẢN PHÂN TÍCH — CHƯA THỂ CHẾ TÁC</strong>
                  <span>
                    Các mảnh đang thấy chưa phải tấm cắt hoàn chỉnh. Cần nối về
                    khung và xử lý chi tiết quá mảnh.
                  </span>
                  <button
                    className="button primary"
                    disabled={disabled}
                    onClick={() =>
                      run(() => generate(project!), "Đang tạo bản chế tác")
                    }
                  >
                    Tạo bản chế tác đạt kiểm tra
                  </button>
                </div>
              )}
            <div className="canvas-toolbar">
              <div className="view-tabs">
                <button
                  aria-label="Xem phẳng"
                  className={view === "flat" ? "active" : ""}
                  onClick={() => setView("flat")}
                >
                  <Layers size={14} />
                  <span>Phẳng</span>
                </button>
                <button
                  aria-label="Xem lắp ráp"
                  className={view === "assembled" ? "active" : ""}
                  onClick={() => setView("assembled")}
                >
                  <Box size={14} />
                  <span>Lắp ráp</span>
                </button>
                <button
                  aria-label="Xem tách lớp"
                  className={view === "exploded" ? "active" : ""}
                  onClick={() => setView("exploded")}
                >
                  <Layers size={14} />
                  <span>Tách lớp</span>
                </button>
                {(config.mode === "lightbox" ||
                  workflow.lighting === "backlit") && (
                  <button
                    aria-label="Xem ánh sáng"
                    className={view === "lightbox" ? "active" : ""}
                    onClick={() => setView("lightbox")}
                  >
                    <Lightbulb size={14} />
                    <span>Ánh sáng</span>
                  </button>
                )}
                <button
                  aria-label="Xem ảnh gốc"
                  className={view === "original" ? "active" : ""}
                  onClick={() => setView("original")}
                >
                  <ImagePlus size={14} />
                  <span>Ảnh gốc</span>
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
                    alt="So sánh ảnh gốc"
                  />
                ) : view === "flat" ? (
                  <div
                    className="paper"
                    style={{
                      aspectRatio: `${project.width_mm}/${project.height_mm}`,
                      transform: `translate(${pan.x}px,${pan.y}px) scale(${zoom})`,
                    }}
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
                      <div className="canvas-empty">Đang mở không gian 3D…</div>
                    }
                  >
                    <Scene
                      project={project}
                      view={view}
                      visible={visible}
                      intensity={intensity}
                      lightColor={lightColor}
                      shadows={showShadows}
                      lightMode={lightMode}
                      transmittance={transmittance}
                    />
                  </Suspense>
                )
              ) : (
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
                  <span className="sample-caption">
                    MOUNTAIN STUDY <i /> Mẫu minh họa
                  </span>
                </div>
              )}
              {!layers.length && (
                <div className="canvas-prompt">
                  <span>Ý tưởng của bạn bắt đầu từ đây</span>
                  <small>Tải ảnh lên hoặc thử ảnh mẫu để khám phá.</small>
                </div>
              )}
              <span className="corner-label">
                {layers.length
                  ? `${project?.width_mm.toFixed(0)} × ${project?.height_mm.toFixed(0)} mm`
                  : "KHÔNG GIAN SÁNG TẠO"}
              </span>
              {(view === "flat" || view === "original") && (
                <div className="zoom-controls">
                  <button
                    aria-label="Thu nhỏ"
                    onClick={() => setZoom((z) => Math.max(0.3, z - 0.1))}
                  >
                    <ZoomOut size={15} />
                  </button>
                  <span>{Math.round(zoom * 100)}%</span>
                  <button
                    aria-label="Phóng to"
                    onClick={() => setZoom((z) => Math.min(3, z + 0.1))}
                  >
                    <ZoomIn size={15} />
                  </button>
                  <button
                    aria-label="Đặt lại góc nhìn"
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
                <Lightbulb size={16} />
                <label>
                  Nguồn màu
                  <select
                    aria-label="Nguồn màu ánh sáng"
                    value={lightMode}
                    onChange={(e) => setLightMode(e.target.value)}
                  >
                    <option value="source">Theo vùng màu ảnh gốc</option>
                    <option value="uniform">Đèn một màu</option>
                  </select>
                </label>
                <label>
                  Cường độ ánh sáng
                  <input
                    aria-label="Cường độ ánh sáng"
                    type="range"
                    min=".2"
                    max="5"
                    step=".1"
                    value={intensity}
                    onChange={(e) => setIntensity(+e.target.value)}
                  />
                </label>
                {lightMode === "uniform" && (
                  <label>
                    Màu ánh sáng
                    <input
                      aria-label="Màu ánh sáng"
                      type="color"
                      value={lightColor}
                      onChange={(e) => setLightColor(e.target.value)}
                    />
                  </label>
                )}
                {lightMode === "source" && (
                  <label>
                    Độ xuyên sáng mỗi tấm (ước lượng)
                    <input
                      aria-label="Độ xuyên sáng giấy"
                      type="range"
                      min=".15"
                      max=".95"
                      step=".05"
                      value={transmittance}
                      onChange={(e) => setTransmittance(+e.target.value)}
                    />
                    <span>{Math.round(transmittance * 100)}%</span>
                  </label>
                )}
                <small>
                  {lightMode === "source"
                    ? "Mô phỏng tấm lọc màu theo ảnh + giấy xuyên sáng. Cần in tấm lọc màu và hiệu chuẩn vật liệu thực tế."
                    : "Mô phỏng ánh sáng gần đúng"}
                </small>
              </div>
            )}
            {project && layers.length > 0 && view !== "original" && (
              <div className="preview-guides">
                <span>
                  {view === "flat" && (selected || visible.size === 1)
                    ? "Gạch chéo: phần bỏ · Màu giấy: phần giữ"
                    : "Xem chồng lớp · Chọn một lớp để xem vùng bỏ"}
                  {selected && view === "flat" ? ` · ${selected}` : ""}
                </span>
                <label>
                  <input
                    type="checkbox"
                    aria-label="Đổ bóng giữa các lớp"
                    checked={showShadows}
                    onChange={(e) => setShowShadows(e.target.checked)}
                  />{" "}
                  Đổ bóng
                </label>
              </div>
            )}
            <div className="canvas-footer">
              <span>
                <span className="tiny-square" />
                {layers.length
                  ? `${layers.length} lớp giấy · ${config.mode === "lightbox" ? "Lightbox" : "Multicolor papercut"}`
                  : "Tạo từ ảnh của bạn"}
              </span>
              <span>
                {view === "flat"
                  ? "Cuộn để zoom · Kéo để di chuyển"
                  : "Kéo để xoay · Cuộn để zoom"}
              </span>
            </div>
          </section>

          <aside className="layers-panel panel">
            <div className="panel-title">
              <span className="step">02</span>
              <h2>Các lớp giấy</h2>
              <span className="count">{layers.length}</span>
            </div>
            <div className="right-tabs">
              <button
                className={tab === "layers" ? "active" : ""}
                onClick={() => setTab("layers")}
              >
                Lớp giấy
              </button>
              <button
                className={tab === "issues" ? "active" : ""}
                onClick={() => setTab("issues")}
              >
                Kiểm tra {findings.length > 0 && <span>{findings.length}</span>}
              </button>
            </div>
            <div className="layer-list">
              {tab === "layers" ? (
                layers.length ? (
                  <>
                    <p className="list-direction">PHÍA TRƯỚC → PHÍA SAU</p>
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
                          aria-label={`Xem riêng ${layer.id}`}
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
                          <strong>Lớp {index + 1}</strong>
                          <span>{layer.color.toUpperCase()}</span>
                          <input
                            type="color"
                            aria-label={`Màu lớp ${layer.id}`}
                            value={layer.color}
                            disabled={disabled}
                            onChange={(e) =>
                              run(
                                () =>
                                  edit("set_color", {
                                    layer_id: layer.id,
                                    color: e.target.value,
                                  }),
                                "Đang đổi màu",
                              )
                            }
                          />
                        </div>
                        <div className="layer-actions">
                          <button
                            aria-label={`Ẩn hiện ${layer.id}`}
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
                            aria-label={`Đưa ${layer.id} lên trước`}
                            disabled={disabled || index === 0}
                            onClick={() => {
                              const ids = layers.map((l) => l.id);
                              [ids[index - 1], ids[index]] = [
                                ids[index],
                                ids[index - 1],
                              ];
                              run(
                                () => edit("reorder", { layer_ids: ids }),
                                "Đang đổi thứ tự",
                              );
                            }}
                          >
                            <ArrowUp size={12} />
                          </button>
                          <button
                            aria-label={`Đưa ${layer.id} ra sau`}
                            disabled={disabled || index === layers.length - 1}
                            onClick={() => {
                              const ids = layers.map((l) => l.id);
                              [ids[index], ids[index + 1]] = [
                                ids[index + 1],
                                ids[index],
                              ];
                              run(
                                () => edit("reorder", { layer_ids: ids }),
                                "Đang đổi thứ tự",
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
                      Hiện tất cả các lớp
                    </button>
                  </>
                ) : (
                  <div className="empty-layers">
                    <Layers size={30} strokeWidth={1} />
                    <strong>Mỗi lớp, một câu chuyện.</strong>
                    <p>
                      Các tấm giấy sẽ xuất hiện ở đây sau khi bạn tạo thiết kế.
                    </p>
                    <span>01 — 02 — 03</span>
                  </div>
                )
              ) : (
                <div className="issue-list">
                  {!project?.report_current ? (
                    <p>
                      Kiểm tra phiên bản hiện tại để xem các vấn đề chế tác.
                    </p>
                  ) : !findings.length ? (
                    <p className="all-clear">
                      <ShieldCheck size={25} /> Không phát hiện vi phạm trong
                      phạm vi kiểm tra phần mềm.
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
                <strong>Khả năng chế tác</strong>
                <span
                  className={`status-dot ${currentPass ? "green" : layers.length ? "amber" : ""}`}
                />
              </div>
              <p className={`status-text ${currentPass ? "pass" : ""}`}>
                {status}
              </p>
              <small>
                {layers.length
                  ? "Kiểm tra phần mềm · Chưa thử cắt thực tế"
                  : "Tạo thiết kế để kiểm tra đường cắt và kết cấu."}
              </small>
              <button
                className="button secondary"
                disabled={disabled || !layers.length}
                onClick={() => run(validate, "Đang kiểm tra")}
              >
                <ScanLine size={14} /> Kiểm tra thiết kế
              </button>
              <button
                className="text-button"
                disabled={disabled || !layers.length}
                onClick={() => run(() => edit("repair"), "Đang sửa hình học")}
              >
                Tự động nối đảo & sửa chi tiết mỏng
              </button>
              <details>
                <summary>Thông số vật liệu</summary>
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
                          min_feature_width_mm: "Chi tiết tối thiểu",
                          min_bridge_width_mm: "Cầu nối tối thiểu",
                          min_hole_size_mm: "Lỗ cắt tối thiểu",
                          thickness_mm: "Độ dày giấy",
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
                          "Đang đổi vật liệu",
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
                  <summary>Cầu nối tự động</summary>
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
                                "Đang chỉnh cầu nối",
                              )
                            }
                          />{" "}
                          {record.layer_id} · cầu {record.index + 1}
                        </label>
                        <label>
                          Độ rộng{" "}
                          <input
                            aria-label={`Độ rộng cầu ${record.index + 1}`}
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
                                  "Đang chỉnh cầu nối",
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
                run(() => edit("convert_legacy"), "Đang chuyển bản cũ")
              }
            >
              Chuyển bản cũ sang lớp liền khung
            </button>
          )}
          <div className="export-icon">
            <Download size={20} />
          </div>
          <div>
            <h3>Mang thiết kế ra đời thực.</h3>
            <p>
              PDF in 100% · kiểm tra thước trước khi cắt · chưa thử cắt giấy
              thực tế.
            </p>
            {project?.material_accounting && (
              <small>
                {project.material_accounting.cut_sheet_count} tấm cắt ·{" "}
                {project.material_accounting.colors_used} màu ·{" "}
                {project.material_accounting.uncut_backing_count} nền ·{" "}
                {project.material_accounting.diffuser_count} tán sáng ·{" "}
                {project.material_accounting.spacer_count} bộ cách lớp
              </small>
            )}
          </div>
          <div className="export-options">
            <select
              aria-label="Khổ giấy in"
              value={printPaper}
              onChange={(e) => setPrintPaper(e.target.value)}
            >
              <option>A4</option>
              <option>A3</option>
            </select>
            <select
              aria-label="Hướng giấy in"
              value={printOrientation}
              onChange={(e) => setPrintOrientation(e.target.value)}
            >
              <option value="auto">Hướng tự động</option>
              <option value="portrait">Dọc</option>
              <option value="landscape">Ngang</option>
            </select>
            <select
              aria-label="Mặt in"
              value={printSide}
              onChange={(e) => setPrintSide(e.target.value)}
            >
              <option value="front">In mặt trước</option>
              <option value="back">In mặt sau · lật gương</option>
            </select>

            <select
              aria-label="Định dạng xuất thêm"
              defaultValue=""
              disabled={disabled || !layers.length}
              onChange={(e) => {
                if (e.target.value)
                  run(() => download(e.target.value), "Đang chuẩn bị file");
                e.target.value = "";
              }}
            >
              <option value="" disabled>
                Xuất riêng...
              </option>
              <option value="light_filter">PDF tấm lọc màu theo ảnh gốc</option>
              <option value="pdf">PDF tất cả lớp · tỉ lệ 1:1</option>
              <option value="cut_svg">SVG đường cắt lớp đang chọn</option>
              <option value="svg">SVG vật liệu lớp đang chọn</option>
              <option value="dxf">DXF lớp đang chọn</option>
              <option value="png">PNG preview</option>
              <option value="report">Validation report</option>
            </select>
            <button
              className="button secondary"
              disabled={disabled || !layers.length}
              onClick={() => run(() => download("zip"), "Đang chuẩn bị ZIP")}
            >
              <Download size={15} /> Tải ZIP bản nháp
            </button>
            <button
              className="button primary"
              disabled={disabled || !currentPass}
              onClick={() =>
                run(
                  () => download("zip", "cut_ready"),
                  "Đang chuẩn bị file cắt",
                )
              }
            >
              <ShieldCheck size={16} /> Xuất cut-ready
            </button>
          </div>
        </section>
        <footer>
          <span>Được tạo nên từ hình học. Hoàn thiện bằng đôi tay.</span>
          <span>
            Geometry-first <i /> Local-first <i /> PaperCraft AI
          </span>
        </footer>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
