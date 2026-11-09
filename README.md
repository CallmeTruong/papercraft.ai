<div align="center">

# PaperCraft AI

**Turn photos into layered paper art.**

A local studio for frame-connected papercuts, multicolor compositions and lightboxes.

[Get started](#get-started) · [Demo gallery](demo-assets/README.md) · [Workflow](#how-it-works) · [Specifications](PaperCraft_AI/specs/README.md)

![PaperCraft AI studio walkthrough](demo-assets/animation/studio-tour.gif)

</div>

Design in physical units, inspect every sheet, preview the stack and export templates for cutting. Optional local models suggest object masks, detail regions and relative depth. Geometry algorithms construct and validate the paper sheets.

## See the results

![Three photographs and their actual workflow outputs](demo-assets/gallery/all-modes.png)

Real runs on three user-provided images. This comparison shows actual cut geometry and light previews **without an original-photo insert**. Results are stylized: fine fur, scales and small facial details can be lost. Geometry PASS does not certify visual identity or a physical cutting trial. Settings, rejected attempts and model fallbacks are recorded in the [demo collection](demo-assets/README.md).

## Choose your paper format

| Mode | What you make | Useful previews |
| --- | --- | --- |
| **Stencil** | A connected cut sheet over a contrasting backing | Flat design and isolated cutouts |
| **Multicolor** | Ordered sheets of colored paper | Flat composition and assembled stack |
| **Depth Scene: colored paper** | Spaced colored sheets with visible relief | Assembled and exploded views |
| **Depth Scene: white paper** | Spaced white sheets for backlighting | Light transmission and broad color zones |

The **original photo insert** adds a separate image plane above the light source. It is not photographic detail recovered in the cut sheets. This preview option starts enabled and can be switched off to inspect the paper design alone.

## Get started

Requirements: **Python 3.12+**, **uv**, **Node.js 22+** and **npm**. Run from the repository root.

```powershell
uv sync --frozen
cd frontend
npm ci
npm run build
cd ..
uv run papercraft-ai serve
```

Open **http://127.0.0.1:8000**. API docs: **http://127.0.0.1:8000/docs**.

1. Upload an image. The first design starts automatically.
2. Choose a mode, paper colors, layer budget and physical size. Protect important regions when needed.
3. Regenerate after changing generation settings. Inspect individual sheets and the fabrication report.
4. Preview assembly or lighting, then download a current cut-ready revision.

The UI exposes the generation time limit. More time lets the bounded search continue; it does not guarantee a feasible design.

### Optional local AI

AI switches start enabled in a fresh UI session. Install optional dependencies and prepare model files before using them:

```powershell
uv sync --extra ai
uv run --extra ai python -m papercraft_ai.ai.setup all
uv run --extra ai papercraft-ai serve
```

| Component | Contribution | Scope |
| --- | --- | --- |
| **SAM 2.1 Tiny** | Object-mask proposals | Object regions when usable masks are found |
| **Florence-2 Base** | Captioned region proposals | Guidance for structural detail selection |
| **MediaPipe Face Landmarker** | Human facial landmarks | Human faces, not a general pet-feature detector |
| **Depth Anything V2 Small** | Relative depth cues | Where applicable in Depth Scene |

Inference runs locally and is serialized to limit memory use. Weights are downloaded only by the explicit setup command, into `.papercraft/models`. Missing models, low memory or unusable predictions can trigger algorithmic fallback; inspect AI diagnostics. An enabled switch does not mean the model contributed successfully.

## Inspect before cutting

![Annotated single-sheet view and waste regions](demo-assets/ui/04-cut-waste-annotated.png)

Every production sheet must retain a closed frame and connect its retained shapes to that frame. The checker evaluates continuous geometry, including disconnected pieces, narrow supports and small cutouts, against the fabrication profile. A failed design is not exported as cut-ready.

The report separates **geometry** from **image fidelity**. A design can pass manufacturing checks while still requiring visual review. Material strength, handling and lighting need a physical trial.

## Explore depth and light

![Annotated light controls](demo-assets/ui/07-light-controls-annotated.png)

Assembled and exploded views reveal layer order and spacing. Light preview supports broad image-derived color zones, uniform lighting, source intensity, paper transmission and the optional photo insert. The light source uses simplified color regions rather than the original photo as its texture.

Lighting is approximate: material transmission, ink, diffusion and real LEDs are not calibrated. Colored ambient Depth Scene uses color-occlusion relief to preserve visible artwork; it is not an exact 3D reconstruction from one photograph.

## Export and assemble

| Output | Purpose |
| --- | --- |
| **PDF** | Actual-size templates, A4/A3 layout, optional tiling and alignment marks |
| **SVG / DXF** | Cut contours for vector or cutter workflows |
| **Material SVG / PNG** | Retained-paper geometry and visual previews |
| **Project JSON** | Portable source, settings and saved geometry |
| **ZIP** | Sheets, report and assembly instructions |

In printable templates, **diagonal hatching marks material to remove**. Cut along the solid contour, not along hatch lines. Retained regions are unhatched; no instruction text is printed on templates.

Print at **100% / Actual Size**, disable fit-to-page and verify the 20 mm ruler. Layer 0 is the front; assemble from back to front. Changes to geometry, order, colors or material profile require revalidation before cut-ready export.

Authored examples: [Multicolor PDF](artifacts/reference/multicolor/print/all-layers.pdf) · [Lightbox PDF](artifacts/reference/lightbox/print/all-layers.pdf).

## How it works

![Source, AI guidance, simplification, sheet construction, validation and export](demo-assets/workflow/papercraft-workflow.png)

This diagram illustrates component roles, not measured inference results. Model participation depends on availability and mode.

`Input -> optional perception -> detail-aware abstraction -> ordered sheets -> frame connections -> geometry and fidelity checks -> preview and export`

Preview and export share the design geometry. AI suggestions do not bypass fabrication checks. Authored ScenePlans support additional compositions; automatic generative styling for Splash and Portal is not implemented.

## Demo materials

The [demo folder](demo-assets/README.md) contains the workflow illustration, source images, mode comparisons, annotated screenshots, animated walkthrough, portable projects, cut-ready bundles and run logs. Reproduction scripts are in [scripts](scripts/).

The images were supplied for this demo; redistribution rights have not been established. Review image rights before publishing the collection. No physical cut-and-assembly test is claimed.

## Development

```powershell
# Backend
uv run papercraft-ai serve

# Frontend, in a second terminal
cd frontend
npm run dev
```

Vite proxies API requests to port 8000. Projects are stored under `.papercraft/`; **Save project** downloads portable JSON. Do not commit personal project storage or model weights.

```powershell
uv run pytest -q
uv run ruff check src tests
cd frontend
npm run build
npx playwright install chromium
npm test
```

Browser tests start a separate backend on port 8001. See [implementation status](docs/implementation-status.md) and the [SpecKit roadmap](PaperCraft_AI/specs/README.md) for scope and remaining limitations.
