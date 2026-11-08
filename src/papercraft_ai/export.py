"""Vector serializers. Export paths are based on application-generated layer ids."""

import hashlib
import io
import json
import zipfile
from xml.sax.saxutils import escape

import ezdxf

from .geometry import polygon_parts


def geometry_path(geometry):
    paths = []
    for polygon in polygon_parts(geometry):
        for ring in [polygon.exterior, *polygon.interiors]:
            coords = list(ring.coords)
            paths.append("M " + " L ".join(f"{x:.6f},{y:.6f}" for x, y in coords[:-1]) + " Z")
    return " ".join(paths)


def svg_document(
    geometry, width_mm, height_mm, color="#000000", layer_id="layer", *, cut_only=False
):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm:g}mm" '
        f'height="{height_mm:g}mm" viewBox="0 0 {width_mm:g} {height_mm:g}">'
        f'<path id="{escape(layer_id)}" d="{geometry_path(geometry)}" '
        + (
            'fill="none" stroke="#000000" stroke-width="0.15" data-operation="CUT"/></svg>'
            if cut_only
            else f'fill="{escape(color)}" fill-rule="evenodd" stroke="none"/></svg>'
        )
    )


def dxf_document(geometry, height_mm):
    document = ezdxf.new("R2010")
    document.units = 4  # millimetres
    document.header["$INSUNITS"] = 4
    document.layers.new("CUT")
    model = document.modelspace()
    for polygon in polygon_parts(geometry):
        for ring in [polygon.exterior, *polygon.interiors]:
            model.add_lwpolyline(
                [(x, height_mm - y) for x, y in list(ring.coords)[:-1]],
                close=True,
                dxfattribs={"layer": "CUT"},
            )
    stream = io.StringIO()
    document.write(stream)
    return stream.getvalue()


def assert_exportable(design, intent):
    from .models import DesignError

    if intent not in ("draft", "cut_ready"):
        raise DesignError("Unknown export intent")
    if intent == "cut_ready":
        if not design.report_is_current():
            raise DesignError("Validate this revision before cut-ready export", "STALE_REVISION")
        if design.report["status"] != "PASS":
            raise DesignError("Cut-ready export requires current PASS", "INVALID_GEOMETRY")


def export_bundle(design, intent="draft", layout=None):
    from .pipeline import png_bytes

    assert_exportable(design, intent)
    from .fabrication.models import PrintLayout
    from .fabrication.printing import print_pdf, tile_layout

    layout = layout or PrintLayout()
    files = {}
    order = []
    for layer, geometry in zip(design.layers, design.geometries):
        svg = f"layers/{layer.id}.svg"
        dxf = f"layers/{layer.id}.dxf"
        files[svg] = svg_document(
            geometry, design.width_mm, design.height_mm, layer.color, layer.id
        ).encode()
        files[dxf] = dxf_document(geometry, design.height_mm).encode()
        files[f"cut/{layer.id}.svg"] = svg_document(
            geometry, design.width_mm, design.height_mm, layer_id=layer.id, cut_only=True
        ).encode()
        files[f"print/{layer.id}.pdf"] = print_pdf(
            [(layer.id, geometry)], design.width_mm, design.height_mm, layout, status=intent.upper()
        )
        order.append(
            {
                "id": layer.id,
                "front_to_back_index": layer.index,
                "color": layer.color,
                "spacing_mm": layer.spacing_mm,
                "attachment": layer.attachment,
                "svg": svg,
                "dxf": dxf,
            }
        )
    files["preview.png"] = png_bytes(design.composite())
    files["print/all-layers.pdf"] = print_pdf(
        [(l.id, g) for l, g in zip(design.layers, design.geometries)],
        design.width_mm,
        design.height_mm,
        layout,
        status=intent.upper(),
    )
    files["print/layout.json"] = json.dumps(
        {
            "settings": layout.model_dump(),
            "tiles_per_layer": tile_layout(design.width_mm, design.height_mm, layout),
        },
        indent=2,
    ).encode()
    if design.light_plan:
        from .fabrication.light import color_diffuser_pdf, full_sheet_source

        files["light/source-color-diffuser.pdf"] = color_diffuser_pdf(design, layout)
        files["light/source-color-diffuser.png"] = png_bytes(
            full_sheet_source(design, max_side=2400, high_resolution=True)
        )
        files["light/materials.json"] = json.dumps(design.light_plan, indent=2).encode()
    files["original.png"] = png_bytes(design.abstraction.original)
    files["project.json"] = json.dumps(design.snapshot(), indent=2, allow_nan=False).encode()
    report = (
        design.report
        if design.report_is_current()
        else {"status": "STALE", "revision_id": design.revision_id}
    )
    files["validation.json"] = json.dumps(report, indent=2, allow_nan=False).encode()
    instructions = [
        "# Assembly guide",
        "",
        f"Export intent: {intent}; software status: {report['status']}.",
        f"Sheet: {design.width_mm:g} × {design.height_mm:g} mm.",
        "The file order is FRONT TO BACK. Assemble from the last layer toward the first.",
        "Mask/path fill is retained paper; enclosed holes are removed.",
        "PDF: print at 100% / Actual size; disable fit to page. Verify the 20 mm ruler before cutting.",
        "Black outline: cut. Material SVG fill: keep. Enclosed holes: remove. Keep the entire closed frame.",
        "PDF guide: white material is retained; light diagonal hatch marks waste to REMOVE.",
        "Cut along the solid black contours only; diagonal hatch and gray alignment marks are guides.",
        "CUT SVG/DXF contain cutting paths only. PDFs have no printed text; ruler/alignment marks are guides.",
        "PASS applies to declared continuous software checks. Physical cutting: NOT_RUN.",
        "",
    ]
    instructions += [
        f"{i + 1}. {item['id']}: {item['color']}, spacing {item['spacing_mm']:g} mm"
        + (f"; {item['attachment']}" if item["attachment"] else "")
        for i, item in enumerate(order)
    ]
    files["assembly.md"] = "\n".join(instructions).encode()
    if design.light_plan:
        instructions += [
            "",
            "## Color light assembly",
            "Print light/source-color-diffuser.pdf at 100% on a translucent color-printable diffuser.",
            "Use white translucent cut paper, not opaque cardstock; mount the color diffuser behind the cut stack with a white backlight.",
            f"Preview assumes per-sheet light transmission {design.light_plan['paper_transmittance_assumption']:.0%}; calibrate the actual paper/ink/light before matching colors.",
            "The colored diffuser is one uncut part, already included in diffuser_count; colored_diffuser_count is its subtype, not an additional sheet.",
            "Structural PASS does not certify optical color accuracy. Optical/physical tests: NOT_RUN.",
        ]
        files["assembly.md"] = "\n".join(instructions).encode()
    manifest = {
        "schema_version": 1,
        "revision_id": design.revision_id,
        "intent": intent,
        "status": report["status"],
        "units": "mm",
        "width_mm": design.width_mm,
        "height_mm": design.height_mm,
        "frame": design.frame.spec.model_dump(),
        "material_accounting": design.material_accounting(),
        "light_plan": design.light_plan,
        "art_review_status": "pending_review",
        "physical_test_status": "NOT_RUN",
        "layers": order,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    files["manifest.json"] = json.dumps(manifest, indent=2).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return stream.getvalue()
