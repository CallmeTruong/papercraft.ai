"""Vector cutting templates in physical units, tiled without fit-to-page scaling."""

import io
import math

from reportlab.pdfgen import canvas
from shapely.affinity import scale
from shapely.geometry import box

from .models import PrintLayout
from .support import parts

MM = 72 / 25.4


def polygon_path(document, geometry, xy):
    path = document.beginPath()
    for polygon in parts(geometry):
        for ring in [polygon.exterior, *polygon.interiors]:
            coords = list(ring.coords)
            path.moveTo(*xy(*coords[0]))
            for point in coords[1:]:
                path.lineTo(*xy(*point))
            path.close()
    return path


def waste_guides(document, geometry, width_mm, height_mm, xy):
    waste = box(0, 0, width_mm, height_mm).difference(geometry)
    if not waste.is_empty:
        document.saveState()
        # Even-odd clipping protects retained islands nested inside a removed region.
        document.clipPath(polygon_path(document, waste, xy), stroke=0, fill=0, fillMode=0)
        document.setStrokeColorRGB(0.78, 0.78, 0.78)
        document.setLineWidth(0.09 * MM)
        # Fixed sheet coordinates keep the hatch aligned across overlapping print pages.
        for offset in range(0, math.ceil(width_mm + height_mm) + 4, 4):
            document.line(*xy(offset, 0), *xy(offset - height_mm, height_mm))
        document.restoreState()


def tile_layout(width_mm, height_mm, layout=None):
    layout = layout or PrintLayout()
    size = (210, 297) if layout.paper == "A4" else (297, 420)
    choices = (
        [size, size[::-1]]
        if layout.orientation == "auto"
        else [size if layout.orientation == "portrait" else size[::-1]]
    )
    candidates = []
    for pw, ph in choices:
        uw, uh = pw - 2 * layout.margin_mm, ph - 2 * layout.margin_mm - 12
        if min(uw, uh) <= layout.overlap_mm:
            raise ValueError("Print overlap must be smaller than the printable window")
        nx = max(1, math.ceil((width_mm - layout.overlap_mm) / (uw - layout.overlap_mm)))
        ny = max(1, math.ceil((height_mm - layout.overlap_mm) / (uh - layout.overlap_mm)))
        candidates.append((nx * ny, pw, ph, uw, uh, nx, ny))
    _, pw, ph, uw, uh, nx, ny = min(candidates, key=lambda c: c[0])
    return [
        {
            "paper_width_mm": pw,
            "paper_height_mm": ph,
            "usable_width_mm": uw,
            "usable_height_mm": uh,
            "origin_x_mm": col * (uw - layout.overlap_mm),
            "origin_y_mm": row * (uh - layout.overlap_mm),
            "row": row + 1,
            "column": col + 1,
            "rows": ny,
            "columns": nx,
        }
        for row in range(ny)
        for col in range(nx)
    ]


def print_pdf(layers, width_mm, height_mm, layout=None, *, status="DRAFT"):
    layout = layout or PrintLayout()
    stream = io.BytesIO()
    document = canvas.Canvas(stream, pageCompression=1)
    document.setTitle("PaperCraft AI - 1:1 cutting templates")
    for layer_id, geometry in layers:
        if layout.print_side == "back":
            geometry = scale(geometry, xfact=-1, yfact=1, origin=(width_mm / 2, 0))
        tiles = tile_layout(width_mm, height_mm, layout)
        for tile in tiles:
            pw, ph = tile["paper_width_mm"], tile["paper_height_mm"]
            margin, x0, y0 = layout.margin_mm, tile["origin_x_mm"], tile["origin_y_mm"]
            document.setPageSize((pw * MM, ph * MM))
            top = margin + 12

            def xy(x, y, margin=margin, x0=x0, ph=ph, top=top, y0=y0):
                return (margin + x - x0) * MM, (ph - top - y + y0) * MM

            # Clip drawing, not geometry: clipping polygons would add false cutting edges at tile seams.
            document.saveState()
            clip = document.beginPath()
            clip.rect(
                margin * MM,
                (ph - top - tile["usable_height_mm"]) * MM,
                tile["usable_width_mm"] * MM,
                tile["usable_height_mm"] * MM,
            )
            document.clipPath(clip, stroke=0, fill=0)
            waste_guides(document, geometry, width_mm, height_mm, xy)
            document.setStrokeColorRGB(0, 0, 0)
            document.setLineWidth(0.15 * MM)
            for polygon in parts(geometry):
                for ring in [polygon.exterior, *polygon.interiors]:
                    path = document.beginPath()
                    coords = list(ring.coords)
                    path.moveTo(*xy(*coords[0]))
                    for point in coords[1:]:
                        path.lineTo(*xy(*point))
                    path.close()
                    document.drawPath(path, stroke=1, fill=0)
            if len(tiles) > 1 and layout.overlap_mm > 0:
                # Matching global-coordinate crosses occur on both overlapping pages.
                marks = set()
                for col in range(1, tile["columns"]):
                    gx = col * (tile["usable_width_mm"] - layout.overlap_mm) + layout.overlap_mm / 2
                    for gy in range(20, math.ceil(height_mm), 40):
                        marks.add((gx, float(gy)))
                for row in range(1, tile["rows"]):
                    gy = (
                        row * (tile["usable_height_mm"] - layout.overlap_mm) + layout.overlap_mm / 2
                    )
                    for gx in range(20, math.ceil(width_mm), 40):
                        marks.add((float(gx), gy))
                document.setStrokeColorRGB(0.55, 0.55, 0.55)
                document.setFillColorRGB(0.45, 0.45, 0.45)
                for gx, gy in sorted(marks):
                    if (
                        x0 + 2 < gx < x0 + tile["usable_width_mm"] - 2
                        and y0 + 2 < gy < y0 + tile["usable_height_mm"] - 2
                    ):
                        px, py = xy(gx, gy)
                        document.line(px - 2 * MM, py, px + 2 * MM, py)
                        document.line(px, py - 2 * MM, px, py + 2 * MM)
                document.setFillColorRGB(0, 0, 0)
            document.restoreState()
            # Ruler is a guide in the reserved header, never a CUT path.
            document.setStrokeColorRGB(0.3, 0.3, 0.3)
            document.setFillColorRGB(0, 0, 0)
            rx = pw - margin - layout.ruler_mm
            ry = ph - margin - 3
            document.line(rx * MM, ry * MM, (rx + layout.ruler_mm) * MM, ry * MM)
            document.line(rx * MM, (ry - 1) * MM, rx * MM, (ry + 1) * MM)
            document.line(
                (rx + layout.ruler_mm) * MM,
                (ry - 1) * MM,
                (rx + layout.ruler_mm) * MM,
                (ry + 1) * MM,
            )
            document.showPage()
    document.save()
    return stream.getvalue()
