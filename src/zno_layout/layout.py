from __future__ import annotations

import math
import heapq

from .model import Gate, Layout, Netlist, Rect
from .pdk import PDK


def _rram_shapes(netlist: Netlist, pdk: PDK) -> tuple[list[Rect], list[tuple[float, float, float, float]]]:
    pixel = pdk.pixel_pitch_um
    shapes: list[Rect] = []
    boxes: list[tuple[float, float, float, float]] = []
    cursor_y = 4
    for memory in netlist.rrams:
        bits_per_stack = memory.stack.count("ZnO") * pdk.rram_bits_per_zno
        physical_cells = math.ceil(memory.bits / bits_per_stack)
        if memory.stack == ("X", "ZnO", "Y"):
            mask_layers = ("rram_x", "rram_zno", "rram_y")
        else:
            mask_layers = (
                "rram_x_bottom", "rram_zno_bottom", "rram_y",
                "rram_zno_top", "rram_x_top",
            )
        columns = max(1, min(32, math.ceil(math.sqrt(physical_cells))))
        rows = math.ceil(physical_cells / columns)
        for index in range(physical_cells):
            column, row = index % columns, index // columns
            x1p = 4 + column * (pdk.rram_cell_width_p + 1)
            y1p = cursor_y + row * (pdk.rram_cell_height_p + 1)
            x1, y1 = x1p * pixel, y1p * pixel
            x2 = x1 + pdk.rram_cell_width_p * pixel
            y2 = y1 + pdk.rram_cell_height_p * pixel
            lo = index * bits_per_stack
            hi = min(memory.bits - 1, lo + bits_per_stack - 1)
            label = f"{memory.name}[{hi}:{lo}] {'/'.join(memory.stack)}"
            for layer in mask_layers:
                shapes.append(Rect(layer, x1, y1, x2, y2, label))
            boxes.append((x1, y1, x2, y2))
        cursor_y += rows * (pdk.rram_cell_height_p + 1) + 2
    return shapes, boxes


def _cell_shapes(gate: Gate, pdk: PDK) -> tuple[list[Rect], dict[str, tuple[int, int]]]:
    if gate.kind not in {"NOT", "NAND", "NOR", "LEVEL_SHIFTER_UP", "BIAS_DRIVER"}:
        raise ValueError(f"{gate.kind} was not expanded into an NMOS primitive")
    pixel = pdk.pixel_pitch_um
    ox, oy = round(gate.x / pixel), round(gate.y / pixel)
    width_p = round(pdk.cell_width_um / pixel)
    count = int(pdk.cells.get(gate.kind, {}).get("tft_count", 2 if gate.kind == "NOT" else 3))
    shapes: list[Rect] = []
    terminals: list[dict[str, tuple[int, int]]] = []

    transistor_step = 5 if count >= 4 else 6
    for index in range(count):
        if gate.kind == "NOR" and index >= 1:
            # Parallel pull-down pair: same vertical D/G/S positions, placed
            # side-by-side so their common drain/source rails never overlap.
            base = oy + 8
            tx = ox + (7 if index == 1 else 13)
        else:
            base = oy + 1 + index * transistor_step
            tx = ox + 8
        shapes.extend([
            Rect("zno", tx * pixel, base * pixel, (tx + 2) * pixel, (base + 5) * pixel, gate.name),
            Rect("source_drain", (tx - 1) * pixel, base * pixel, (tx + 1) * pixel, (base + 1) * pixel, gate.name),
            Rect("source_drain", (tx - 1) * pixel, (base + 4) * pixel, (tx + 1) * pixel, (base + 5) * pixel, gate.name),
            Rect("electrolyte", (tx - 1) * pixel, (base + 2) * pixel, (tx + 2) * pixel, (base + 3) * pixel, gate.name),
            Rect("gate", (tx + 1) * pixel, (base + 2) * pixel, (tx + 2) * pixel, (base + 3) * pixel, gate.name),
        ])
        terminals.append({"D": (tx - 1, base), "G": (tx + 1, base + 2), "S": (tx - 1, base + 4)})

    def connect(net: str, points: list[tuple[int, int]], layer: str = "metal1") -> None:
        hub = points[0]
        for point in points[1:]:
            path = [hub]
            corner = (point[0], hub[1])
            if corner != hub:
                path.append(corner)
            if point != path[-1]:
                path.append(point)
            shapes.extend(_path_rects(path, pixel, net, layer))

    input_cells: list[tuple[int, int]] = []
    for index, input_net in enumerate(gate.inputs):
        switch = terminals[index + 1]
        pin_y = switch["G"][1] + (5 if gate.kind == "NOR" and index == 1 else 0)
        pin = (ox, pin_y)
        input_cells.append(pin)
        # Gate control must never share the S/D output/GND conductor. A small
        # metal1 landing pad and contact joins global routing to the gate layer.
        shapes.append(Rect("metal1", pin[0] * pixel, pin[1] * pixel,
                           (pin[0] + 1) * pixel, (pin[1] + 1) * pixel, input_net))
        connect(input_net, [pin, switch["G"]], "gate")

    output_cell = (ox + width_p - 1, terminals[0]["S"][1])
    vdd_cell = (ox + width_p // 2, oy)
    gnd_cell = (ox + width_p // 2, oy + round(pdk.cell_height_um / pixel) - 1)
    # Enhancement-load TFT: D and G tied to VDD; S is the logic output.
    connect("$VDD", [vdd_cell, terminals[0]["D"], terminals[0]["G"]])
    if gate.kind in {"LEVEL_SHIFTER_UP", "BIAS_DRIVER"}:
        connect(gate.output, [terminals[-1]["D"], output_cell])
        connect("$VDD", [vdd_cell, terminals[0]["D"], terminals[0]["G"]])
        connect("$GND", [terminals[-1]["S"], gnd_cell])
    elif gate.kind == "NOT":
        connect(gate.output, [terminals[0]["S"], terminals[1]["D"], output_cell])
        connect("$GND", [terminals[1]["S"], gnd_cell])
    elif gate.kind == "NAND":
        internal = f"${gate.name}_series"
        connect(gate.output, [terminals[0]["S"], terminals[1]["D"], output_cell])
        connect(internal, [terminals[1]["S"], terminals[2]["D"]])
        connect("$GND", [terminals[2]["S"], gnd_cell])
    else:  # NOR: the two pull-down TFTs are parallel.
        connect(gate.output, [terminals[0]["S"], terminals[1]["D"], terminals[2]["D"], output_cell])
        connect("$GND", [terminals[1]["S"], terminals[2]["S"], gnd_cell])

    pins = {
        **{f"in{index}": ((cell[0] + 0.5) * pixel, (cell[1] + 0.5) * pixel) for index, cell in enumerate(input_cells)},
        "out": ((output_cell[0] + 0.5) * pixel, (output_cell[1] + 0.5) * pixel),
        "vdd": ((vdd_cell[0] + 0.5) * pixel, (vdd_cell[1] + 0.5) * pixel),
        "gnd": ((gnd_cell[0] + 0.5) * pixel, (gnd_cell[1] + 0.5) * pixel),
    }
    return shapes, pins


def _astar_grid(
    start: tuple[int, int], goal: tuple[int, int], width: int, height: int,
    blocked: set[tuple[int, int]], occupied: dict[tuple[int, int], str], net: str,
    clearance: int = 1, max_expansions: int = 50_000,
) -> list[tuple[int, int]] | None:
    """Shortest four-neighbour route. Manhattan heuristic preserves optimality."""
    forbidden: set[tuple[int, int]] = set()
    for (px, py), owner in occupied.items():
        if owner != net:
            for ox in range(-clearance, clearance + 1):
                for oy in range(-clearance, clearance + 1):
                    forbidden.add((px + ox, py + oy))
    initial_h = abs(start[0] - goal[0]) + abs(start[1] - goal[1])
    # Prefer deeper nodes when f is tied. Without this tie-break, an empty
    # 1280x960 canvas makes A* expand a huge diamond before following a direct
    # Manhattan path.
    queue = [(initial_h, 0, 0, start)]
    parent: dict[tuple[int, int], tuple[int, int]] = {}
    cost = {start: 0}
    expansions = 0
    while queue:
        expansions += 1
        if expansions > max_expansions:
            return None
        _, _, distance, point = heapq.heappop(queue)
        if point == goal:
            path = [point]
            while point != start:
                point = parent[point]
                path.append(point)
            return list(reversed(path))
        if distance != cost.get(point):
            continue
        x, y = point
        for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not (0 <= nxt[0] < width and 0 <= nxt[1] < height):
                continue
            if nxt not in {start, goal}:
                if nxt in blocked:
                    continue
                # A one-pixel empty halo prevents edge/corner contact between different nets.
                if nxt in forbidden:
                    continue
            candidate = distance + 1
            if candidate < cost.get(nxt, 1 << 60):
                cost[nxt] = candidate
                parent[nxt] = point
                heuristic = abs(nxt[0] - goal[0]) + abs(nxt[1] - goal[1])
                heapq.heappush(queue, (candidate + heuristic, -candidate, candidate, nxt))
    return None


def _path_rects(path: list[tuple[int, int]], pixel: float, net: str,
                layer: str = "metal1") -> list[Rect]:
    if not path:
        return []
    result: list[Rect] = []
    begin = previous = path[0]
    direction = None
    for point in path[1:] + [path[-1]]:
        new_direction = (point[0] - previous[0], point[1] - previous[1])
        if direction is not None and new_direction != direction:
            x1, y1 = begin
            x2, y2 = previous
            result.append(Rect(layer, min(x1, x2) * pixel, min(y1, y2) * pixel,
                               (max(x1, x2) + 1) * pixel, (max(y1, y2) + 1) * pixel, net))
            begin = previous
        direction = new_direction
        previous = point
    x1, y1 = begin
    x2, y2 = path[-1]
    result.append(Rect(layer, min(x1, x2) * pixel, min(y1, y2) * pixel,
                       (max(x1, x2) + 1) * pixel, (max(y1, y2) + 1) * pixel, net))
    return result


def place_and_route(netlist: Netlist, pdk: PDK) -> Layout:
    pixel = pdk.pixel_pitch_um
    margin = 2 * pixel
    usable = pdk.canvas_width_um - 2 * margin
    per_row = max(1, int(usable // (pdk.cell_width_um + pdk.row_spacing_um)))
    shapes, rram_boxes = _rram_shapes(netlist, pdk)
    net_sources: dict[str, tuple[int, int]] = {}
    pending: list[tuple[str, tuple[int, int]]] = []
    routes: list[tuple[str, tuple[int, int], tuple[int, int]]] = []
    unrouted: list[str] = []

    row_count = max(1, math.ceil(len(netlist.gates) / per_row))
    vertical_gap = max(pdk.row_spacing_um, math.floor(
        (pdk.canvas_height_um - row_count * pdk.cell_height_um) / (row_count + 1) / pixel
    ) * pixel)
    cell_boxes: list[tuple[float, float, float, float]] = list(rram_boxes)
    for index, gate in enumerate(netlist.gates):
        row, column = divmod(index, per_row)
        cells_this_row = min(per_row, len(netlist.gates) - row * per_row)
        horizontal_gap = max(pdk.min_spacing_um, math.floor(
            (pdk.canvas_width_um - cells_this_row * pdk.cell_width_um) / (cells_this_row + 1) / pixel
        ) * pixel)
        gate.x = horizontal_gap + column * (pdk.cell_width_um + horizontal_gap)
        gate.y = vertical_gap + row * (pdk.cell_height_um + vertical_gap)
        cell_boxes.append((gate.x, gate.y, gate.x + pdk.cell_width_um, gate.y + pdk.cell_height_um))
        cell, local_pins = _cell_shapes(gate, pdk)
        shapes.extend(cell)
        for pin_index, net in enumerate(gate.inputs):
            pending.append((net, local_pins[f"in{pin_index}"]))
        pending.append(("$VDD", local_pins["vdd"]))
        pending.append(("$GND", local_pins["gnd"]))
        net_sources[gate.output] = local_pins["out"]

    input_pitch = max(pdk.min_spacing_um + pdk.min_width_um, pdk.canvas_height_um // (len(netlist.inputs) + 1))
    for index, name in enumerate(netlist.inputs):
        y = min(pdk.canvas_height_um - pixel / 2, (index + 1) * input_pitch)
        net_sources[name] = (pixel / 2, math.floor(y / pixel) * pixel + pixel / 2)
    net_sources["$VDD"] = (pixel / 2, pixel / 2)
    net_sources["$GND"] = (pixel / 2, pdk.canvas_height_um - pixel / 2)

    # Every top-level output receives a physical route to a pad at the right edge.
    for index, name in enumerate(netlist.outputs):
        output_y = min(
            pdk.canvas_height_um - pdk.min_width_um,
            (index + 1) * pdk.canvas_height_um // (len(netlist.outputs) + 1),
        )
        pending.append((name, (pdk.canvas_width_um - pixel / 2, math.floor(output_y / pixel) * pixel + pixel / 2)))

    # Reserve continuous power trunks first; signal nets can use the alternate
    # routing plane when crossing a rail.
    pending.sort(key=lambda item: item[0] not in {"$VDD", "$GND"})

    grid_width, grid_height = pdk.image_width_px, pdk.image_height_px
    blocked: set[tuple[int, int]] = set()
    for x1, y1, x2, y2 in cell_boxes:
        for gx in range(int(x1 / pixel) + 1, int(x2 / pixel) - 1):
            for gy in range(int(y1 / pixel) + 1, int(y2 / pixel) - 1):
                blocked.add((gx, gy))
    routing_layers = ("metal2", "metal3", "metal4")
    occupied_by_layer: dict[str, dict[tuple[int, int], str]] = {
        layer: {} for layer in routing_layers
    }
    net_layer: dict[str, str] = {}
    for route_index, (net, destination) in enumerate(pending):
        source = net_sources.get(net)
        if not source:
            unrouted.append(f"{net} -> ({destination[0]},{destination[1]})")
            continue
        start_grid = (int(source[0] // pixel), int(source[1] // pixel))
        goal_grid = (int(destination[0] // pixel), int(destination[1] // pixel))
        clearance = max(1, math.ceil(pdk.min_spacing_um / pixel))
        preferred = net_layer.get(net)
        if preferred:
            candidates = (preferred,) + tuple(layer for layer in routing_layers if layer != preferred)
        elif net == "$VDD":
            candidates = ("metal2", "metal3")
        elif net == "$GND":
            candidates = ("metal3", "metal2")
        else:
            candidates = routing_layers
        path = None
        chosen_layer = ""
        chosen_source = source
        for layer in candidates:
            occupied = occupied_by_layer[layer]
            trial_start = start_grid
            same_net_tree = [point for point, owner in occupied.items() if owner == net]
            if same_net_tree:
                trial_start = min(
                    same_net_tree,
                    key=lambda point: abs(point[0] - goal_grid[0]) + abs(point[1] - goal_grid[1]),
                )
            trial = _astar_grid(trial_start, goal_grid, grid_width, grid_height,
                                set(), occupied, net, clearance)
            if trial is not None:
                path, chosen_layer = trial, layer
                chosen_source = ((trial_start[0] + 0.5) * pixel,
                                 (trial_start[1] + 0.5) * pixel)
                break
        if path is None:
            unrouted.append(f"{net}: no p-grid path from {source} to {destination}")
            continue
        net_layer[net] = chosen_layer
        shapes.extend(_path_rects(path, pixel, net, chosen_layer))
        # Vias are emitted at real cell/pad endpoints. Branches that begin on an
        # existing same-net tree need only the destination via.
        via_points = [goal_grid] if chosen_source != source else [start_grid, goal_grid]
        for vx, vy in via_points:
            via_layers = {
                "metal2": ("via12",),
                "metal3": ("via12", "via23"),
                "metal4": ("via12", "via23", "via34"),
            }[chosen_layer]
            for via_layer in via_layers:
                shapes.append(Rect(via_layer, vx * pixel, vy * pixel,
                                   (vx + 1) * pixel, (vy + 1) * pixel, net))
        for point in path:
            occupied_by_layer[chosen_layer][point] = net
        routes.append((net, chosen_source, destination))

    rows = math.ceil(max(1, len(netlist.gates)) / per_row)
    used_height = margin + rows * (pdk.cell_height_um + pdk.row_spacing_um)
    return Layout(
        pdk.canvas_width_um, max(pdk.canvas_height_um, used_height), shapes,
        net_sources, len(netlist.gates), routes, unrouted,
    )


def _contains(shape: Rect, point: tuple[int, int]) -> bool:
    return shape.x1 <= point[0] <= shape.x2 and shape.y1 <= point[1] <= shape.y2


def _touches(a: Rect, b: Rect) -> bool:
    return a.x1 <= b.x2 and b.x1 <= a.x2 and a.y1 <= b.y2 and b.y1 <= a.y2


def _route_is_connected(layout: Layout, net: str, source: tuple[int, int], sink: tuple[int, int]) -> bool:
    conductors = {"metal1", "via12", "metal2", "via23", "metal3", "via34", "metal4"}
    shapes = [shape for shape in layout.shapes if shape.layer in conductors and shape.label == net]
    frontier = [index for index, shape in enumerate(shapes) if _contains(shape, source)]
    visited = set(frontier)
    while frontier:
        current = frontier.pop()
        if _contains(shapes[current], sink):
            return True
        for index, shape in enumerate(shapes):
            if index not in visited and _touches(shapes[current], shape):
                visited.add(index)
                frontier.append(index)
    return False


def run_drc(layout: Layout, pdk: PDK) -> list[str]:
    errors: list[str] = []
    epsilon = 1e-6
    errors.extend(f"E_UNROUTED {item}" for item in layout.unrouted)
    known_layers = set(pdk.layers)
    for index, shape in enumerate(layout.shapes):
        if shape.layer not in known_layers:
            errors.append(f"E_LAYER shape {index}: unknown layer {shape.layer}")
        if shape.width + epsilon < pdk.min_width_um or shape.height + epsilon < pdk.min_width_um:
            errors.append(f"E_WIDTH shape {index} ({shape.layer}): {shape.width}x{shape.height} um")
        if min(shape.x1, shape.y1) < 0 or shape.x2 > layout.width or shape.y2 > layout.height:
            errors.append(f"E_BOUNDS shape {index} ({shape.layer})")
        for coordinate in (shape.x1, shape.y1, shape.x2, shape.y2):
            units = coordinate / pdk.pixel_pitch_um
            if abs(units - round(units)) > epsilon:
                errors.append(f"E_OFFGRID shape {index} ({shape.layer}): {coordinate} um")
                break
    for net, source, sink in layout.routes:
        if not _route_is_connected(layout, net, source, sink):
            errors.append(f"E_CONNECT {net}: {source} does not reach {sink}")
    route_nets = {net for net, _, _ in layout.routes}
    route_layers = {"metal1", "via12", "metal2", "via23", "metal3", "via34", "metal4"}
    route_shapes = [s for s in layout.shapes if s.layer in route_layers and s.label in route_nets]
    for index, first in enumerate(route_shapes):
        for second in route_shapes[index + 1:]:
            if first.layer == second.layer and first.label != second.label and _touches(first, second):
                errors.append(f"E_SHORT {first.label} touches {second.label}")
    return errors
