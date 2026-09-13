from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

from .model import Layout, Netlist
from .pdk import PDK


def write_svg(layout: Layout, pdk: PDK, path: str | Path) -> None:
    scale = min(1200 / layout.width, 900 / layout.height)
    body = []
    for shape in layout.shapes:
        color = pdk.colors.get(shape.layer, "#ffffff")
        body.append(
            f'<rect x="{shape.x1*scale:.2f}" y="{shape.y1*scale:.2f}" '
            f'width="{shape.width*scale:.2f}" height="{shape.height*scale:.2f}" '
            f'fill="{color}" fill-opacity="0.62"><title>{shape.layer}: {shape.label}</title></rect>'
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{layout.width*scale:.0f}" '
        f'height="{layout.height*scale:.0f}" viewBox="0 0 {layout.width*scale:.2f} {layout.height*scale:.2f}">'
        '<rect width="100%" height="100%" fill="#10131a"/>' + "".join(body) + "</svg>"
    )
    Path(path).write_text(svg, encoding="utf-8")


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


def _write_gray_png(path: Path, pixels: bytearray, width: int, height: int) -> None:
    rows = b"".join(b"\x00" + bytes(pixels[y * width:(y + 1) * width]) for y in range(height))
    payload = b"\x89PNG\r\n\x1a\n"
    payload += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
    payload += _png_chunk(b"IDAT", zlib.compress(rows, 9)) + _png_chunk(b"IEND", b"")
    path.write_bytes(payload)


def write_masks(layout: Layout, pdk: PDK, directory: str | Path) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    sx = pdk.image_width_px / layout.width
    sy = pdk.image_height_px / layout.height
    for layer in pdk.layers:
        pixels = bytearray(pdk.image_width_px * pdk.image_height_px)
        for shape in (s for s in layout.shapes if s.layer == layer):
            x1, x2 = max(0, int(shape.x1 * sx)), min(pdk.image_width_px, max(1, int(shape.x2 * sx)))
            y1, y2 = max(0, int(shape.y1 * sy)), min(pdk.image_height_px, max(1, int(shape.y2 * sy)))
            for y in range(y1, y2):
                start = y * pdk.image_width_px + x1
                pixels[start:start + x2 - x1] = b"\xff" * (x2 - x1)
        _write_gray_png(directory / f"mask_{layer}.png", pixels, pdk.image_width_px, pdk.image_height_px)


def write_netlist(netlist: Netlist, path: str | Path) -> None:
    data = {
        "module": netlist.name, "inputs": netlist.inputs, "outputs": netlist.outputs,
        "gates": [{"kind": g.kind, "name": g.name, "inputs": g.inputs, "output": g.output,
                   "x_um": g.x, "y_um": g.y} for g in netlist.gates],
    }
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
