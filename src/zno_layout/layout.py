from __future__ import annotations

import math

from .model import Gate, Layout, Netlist, Rect
from .pdk import PDK


TFT_COUNT = {"BUF": 1, "NOT": 2, "AND": 4, "OR": 4, "NAND": 3, "NOR": 3, "XOR": 8, "XNOR": 9}


def _cell_shapes(gate: Gate, pdk: PDK) -> tuple[list[Rect], dict[str, tuple[int, int]]]:
    x, y = gate.x, gate.y
    w, h = pdk.cell_width_um, pdk.cell_height_um
    line = pdk.min_width_um
    count = TFT_COUNT[gate.kind]
    shapes = [Rect("metal1", x, y, x + w, y + line, gate.name)]
    step = max(line * 2, (w - 2 * line) // count)
    for index in range(count):
        tx = x + line + index * step
        shapes.extend([
            Rect("zno", tx, y + 2 * line, tx + line, y + h - line, gate.name),
            Rect("source_drain", tx - line // 2, y + 2 * line, tx + 3 * line // 2, y + 3 * line, gate.name),
            Rect("source_drain", tx - line // 2, y + h - 2 * line, tx + 3 * line // 2, y + h - line, gate.name),
            Rect("electrolyte", tx - line // 2, y + h // 2 - line, tx + 3 * line // 2, y + h // 2 + line, gate.name),
            Rect("gate", tx, y + h // 2 - line // 2, tx + line, y + h // 2 + line // 2, gate.name),
        ])
    pins = {f"in{i}": (x, y + (i + 2) * h // (len(gate.inputs) + 3)) for i in range(len(gate.inputs))}
    pins["out"] = (x + w, y + h // 2)
    return shapes, pins


def place_and_route(netlist: Netlist, pdk: PDK) -> Layout:
    margin = pdk.row_spacing_um
    usable = pdk.canvas_width_um - 2 * margin
    per_row = max(1, usable // (pdk.cell_width_um + pdk.row_spacing_um))
    shapes: list[Rect] = []
    net_sources: dict[str, tuple[int, int]] = {}
    pending: list[tuple[str, tuple[int, int]]] = []

    for index, gate in enumerate(netlist.gates):
        row, column = divmod(index, per_row)
        gate.x = margin + column * (pdk.cell_width_um + pdk.row_spacing_um)
        gate.y = margin + row * (pdk.cell_height_um + pdk.row_spacing_um)
        cell, local_pins = _cell_shapes(gate, pdk)
        shapes.extend(cell)
        for pin_index, net in enumerate(gate.inputs):
            pending.append((net, local_pins[f"in{pin_index}"]))
        net_sources[gate.output] = local_pins["out"]

    input_pitch = max(pdk.min_spacing_um + pdk.min_width_um, pdk.canvas_height_um // (len(netlist.inputs) + 1))
    for index, name in enumerate(netlist.inputs):
        net_sources[name] = (0, min(pdk.canvas_height_um - pdk.min_width_um, (index + 1) * input_pitch))

    line = pdk.min_width_um
    for track, (net, destination) in enumerate(pending):
        source = net_sources.get(net)
        if not source:
            continue
        sx, sy = source
        dx, dy = destination
        track_y = max(line, min(pdk.canvas_height_um - line, sy + track * (line + pdk.min_spacing_um)))
        shapes.extend([
            Rect("metal1", min(sx, dx), track_y, max(sx, dx) + line, track_y + line, net),
            Rect("metal1", sx, min(sy, track_y), sx + line, max(sy, track_y) + line, net),
            Rect("metal1", dx, min(dy, track_y), dx + line, max(dy, track_y) + line, net),
        ])

    rows = math.ceil(max(1, len(netlist.gates)) / per_row)
    used_height = margin + rows * (pdk.cell_height_um + pdk.row_spacing_um)
    return Layout(pdk.canvas_width_um, max(pdk.canvas_height_um, used_height), shapes, net_sources, len(netlist.gates))


def run_drc(layout: Layout, pdk: PDK) -> list[str]:
    errors: list[str] = []
    known_layers = set(pdk.layers)
    for index, shape in enumerate(layout.shapes):
        if shape.layer not in known_layers:
            errors.append(f"E_LAYER shape {index}: unknown layer {shape.layer}")
        if shape.width < pdk.min_width_um or shape.height < pdk.min_width_um:
            errors.append(f"E_WIDTH shape {index} ({shape.layer}): {shape.width}x{shape.height} um")
        if min(shape.x1, shape.y1) < 0 or shape.x2 > layout.width or shape.y2 > layout.height:
            errors.append(f"E_BOUNDS shape {index} ({shape.layer})")
    return errors

