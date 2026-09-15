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
    clearance: int = 1, max_expansions: int = 6_000,
    soft_occupied: set[tuple[int, int]] | None = None,
    soft_radius: int = 0, soft_penalty: int = 0,
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
            coupling_cost = 0
            if soft_occupied and soft_penalty:
                x2, y2 = nxt
                # Penalise exact broadside overlap most strongly and nearby
                # parallel adjacency less strongly. It remains a soft cost so
                # routing cannot fail solely because ideal offset is impossible.
                if nxt in soft_occupied:
                    coupling_cost = soft_penalty * 2
                else:
                    for delta in range(1, soft_radius + 1):
                        if ((x2 + delta, y2) in soft_occupied
                                or (x2 - delta, y2) in soft_occupied
                                or (x2, y2 + delta) in soft_occupied
                                or (x2, y2 - delta) in soft_occupied):
                            coupling_cost = max(coupling_cost, soft_penalty // delta)
            candidate = distance + 1 + coupling_cost
            if candidate < cost.get(nxt, 1 << 60):
                cost[nxt] = candidate
                parent[nxt] = point
                heuristic = abs(nxt[0] - goal[0]) + abs(nxt[1] - goal[1])
                heapq.heappush(queue, (candidate + heuristic, -candidate, candidate, nxt))
    return None



def _astar_multilayer(
    start: tuple[int, int, int], goal: tuple[int, int, int],
    width: int, height: int, max_level: int,
    occupied_by_level: dict[int, dict[tuple[int, int], str]],
    net: str, clearance: int, via_cost: int,
    coupling_penalty: int, offset_p: int,
    max_expansions: int = 30_000,
) -> list[tuple[int, int, int]] | None:
    """3-D Manhattan router; vertical moves are adjacent-layer vias."""
    forbidden: dict[int, set[tuple[int, int]]] = {}
    for level in range(2, max_level + 1):
        blocked: set[tuple[int, int]] = set()
        for (px, py), owner in occupied_by_level[level].items():
            if owner == net:
                continue
            for ox in range(-clearance, clearance + 1):
                for oy in range(-clearance, clearance + 1):
                    blocked.add((px + ox, py + oy))
        forbidden[level] = blocked

    def heuristic(state: tuple[int, int, int]) -> int:
        return (abs(state[0] - goal[0]) + abs(state[1] - goal[1])
                + abs(state[2] - goal[2]) * via_cost)

    queue = [(heuristic(start), 0, start)]
    cost = {start: 0}
    parent: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    expansions = 0
    while queue:
        _, distance, state = heapq.heappop(queue)
        if distance != cost.get(state):
            continue
        if state == goal:
            path = [state]
            while state != start:
                state = parent[state]
                path.append(state)
            return list(reversed(path))
        expansions += 1
        if expansions > max_expansions:
            return None
        x, y, level = state
        neighbours = [
            (x + 1, y, level, 1), (x - 1, y, level, 1),
            (x, y + 1, level, 1), (x, y - 1, level, 1),
        ]
        if level > 2:
            neighbours.append((x, y, level - 1, via_cost))
        if level < max_level:
            neighbours.append((x, y, level + 1, via_cost))
        for nx, ny, nl, step in neighbours:
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            point = (nx, ny)
            if (nx, ny, nl) not in {start, goal} and point in forbidden[nl]:
                continue
            extra = 0
            # Broadside and close parallel conductors on adjacent planes are
            # legal only as a last resort, so price them without forbidding.
            for adjacent in (nl - 1, nl + 1):
                if adjacent < 2 or adjacent > max_level:
                    continue
                other = occupied_by_level[adjacent]
                if other.get(point) not in {None, net}:
                    extra += coupling_penalty * 2
                for delta in range(1, offset_p + 1):
                    if any(other.get(candidate) not in {None, net} for candidate in (
                        (nx + delta, ny), (nx - delta, ny),
                        (nx, ny + delta), (nx, ny - delta),
                    )):
                        extra += max(1, coupling_penalty // delta)
                        break
            candidate_cost = distance + step + extra
            nxt = (nx, ny, nl)
            if candidate_cost < cost.get(nxt, 1 << 60):
                cost[nxt] = candidate_cost
                parent[nxt] = state
                heapq.heappush(queue, (candidate_cost + heuristic(nxt), candidate_cost, nxt))
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


def _die_pin_point(number: int, count: int, pdk: PDK) -> tuple[float, float]:
    """Uniform clockwise perimeter placement; DIE pin 0 is top-centre."""
    width, height = pdk.image_width_px, pdk.image_height_px
    top_half = width // 2 - 1
    vertical = height - 2
    horizontal = width - 2
    perimeter = 2 * horizontal + 2 * vertical
    distance = number * perimeter / count
    if distance <= top_half:
        gx, gy = width // 2 + distance, 0
    elif distance <= top_half + vertical:
        gx, gy = width - 1, distance - top_half
    elif distance <= top_half + vertical + horizontal:
        gx, gy = width - 1 - (distance - top_half - vertical), height - 1
    elif distance <= top_half + 2 * vertical + horizontal:
        gx, gy = 0, height - 1 - (distance - top_half - vertical - horizontal)
    else:
        gx, gy = distance - top_half - 2 * vertical - horizontal, 0
    gx, gy = round(gx), round(gy)
    pixel = pdk.pixel_pitch_um
    return ((gx + 0.5) * pixel, (gy + 0.5) * pixel)


def place_and_route(netlist: Netlist, pdk: PDK) -> Layout:
    pixel = pdk.pixel_pitch_um
    margin = 2 * pixel
    usable = pdk.canvas_width_um - 2 * margin
    max_per_row = max(1, int(usable // (pdk.cell_width_um + pdk.min_spacing_um)))
    # Compact connected logic into a centred core instead of spreading every
    # row across the complete die. This shortens the dominant internal nets;
    # package I/O alone travels to the perimeter.
    aspect = pdk.canvas_width_um / pdk.canvas_height_um
    per_row = max(1, min(max_per_row, math.ceil(math.sqrt(max(1, len(netlist.gates)) * aspect))))
    shapes, rram_boxes = _rram_shapes(netlist, pdk)
    net_sources: dict[str, tuple[int, int]] = {}
    pending: list[tuple[str, tuple[int, int]]] = []
    routes: list[tuple[str, tuple[int, int], tuple[int, int]]] = []
    unrouted: list[str] = []

    row_count = max(1, math.ceil(len(netlist.gates) / per_row))
    pitch_x = pdk.cell_width_um + pdk.min_spacing_um
    pitch_y = pdk.cell_height_um + max(pdk.row_spacing_um, pdk.min_spacing_um)
    core_height = row_count * pdk.cell_height_um + (row_count - 1) * (pitch_y - pdk.cell_height_um)
    core_origin_y = max(margin, math.floor(
        (pdk.canvas_height_um - core_height) / 2 / pixel
    ) * pixel)
    cell_boxes: list[tuple[float, float, float, float]] = list(rram_boxes)
    for index, gate in enumerate(netlist.gates):
        row, column = divmod(index, per_row)
        cells_this_row = min(per_row, len(netlist.gates) - row * per_row)
        row_width = cells_this_row * pdk.cell_width_um + (cells_this_row - 1) * pdk.min_spacing_um
        row_origin_x = max(margin, math.floor(
            (pdk.canvas_width_um - row_width) / 2 / pixel
        ) * pixel)
        gate.x = row_origin_x + column * pitch_x
        gate.y = core_origin_y + row * pitch_y
        cell_boxes.append((gate.x, gate.y, gate.x + pdk.cell_width_um, gate.y + pdk.cell_height_um))
        cell, local_pins = _cell_shapes(gate, pdk)
        shapes.extend(cell)
        for pin_index, net in enumerate(gate.inputs):
            pending.append((net, local_pins[f"in{pin_index}"]))
        pending.append(("$VDD", local_pins["vdd"]))
        pending.append(("$GND", local_pins["gnd"]))
        net_sources[gate.output] = local_pins["out"]

    package_points: dict[str, tuple[float, float]] = {}
    if netlist.package and netlist.package.name.startswith("DIE"):
        pad_p = max(1, math.ceil(pdk.external_pad_size_um / pixel))
        for pin in netlist.pin_descriptors:
            edge_point = _die_pin_point(pin.number, netlist.package.pin_count, pdk)
            gx, gy = int(edge_point[0] // pixel), int(edge_point[1] // pixel)
            if gy == 0:  # top: PIN0 is centred here
                x1p, y1p = max(0, min(pdk.image_width_px - pad_p, gx - pad_p // 2)), 0
            elif gx == pdk.image_width_px - 1:  # right
                x1p, y1p = pdk.image_width_px - pad_p, max(0, min(pdk.image_height_px - pad_p, gy - pad_p // 2))
            elif gy == pdk.image_height_px - 1:  # bottom
                x1p, y1p = max(0, min(pdk.image_width_px - pad_p, gx - pad_p // 2)), pdk.image_height_px - pad_p
            else:  # left
                x1p, y1p = 0, max(0, min(pdk.image_height_px - pad_p, gy - pad_p // 2))
            # The global route lands inside the bond/probe pad, while the pad
            # itself remains exactly flush with the die perimeter.
            package_points[pin.signal] = (
                (x1p + pad_p / 2) * pixel,
                (y1p + pad_p / 2) * pixel,
            )
            shapes.append(Rect(
                "metal1", x1p * pixel, y1p * pixel,
                (x1p + pad_p) * pixel, (y1p + pad_p) * pixel,
                f"PIN{pin.number}:{pin.signal}",
            ))

    input_pitch = max(pdk.min_spacing_um + pdk.min_width_um, pdk.canvas_height_um // (len(netlist.inputs) + 1))
    for index, name in enumerate(netlist.inputs):
        y = min(pdk.canvas_height_um - pixel / 2, (index + 1) * input_pitch)
        net_sources[name] = package_points.get(
            name, (pixel / 2, math.floor(y / pixel) * pixel + pixel / 2))
    net_sources["$VDD"] = (pixel / 2, pixel / 2)
    net_sources["$GND"] = (pixel / 2, pdk.canvas_height_um - pixel / 2)
    for pin in netlist.pin_descriptors:
        if pin.kind == "POWER" and pin.signal in package_points:
            net_sources["$VDD"] = package_points[pin.signal]
        elif pin.kind == "GND" and pin.signal in package_points:
            net_sources["$GND"] = package_points[pin.signal]

    # Every top-level output receives a physical route to a pad at the right edge.
    for index, name in enumerate(netlist.outputs):
        output_y = min(
            pdk.canvas_height_um - pdk.min_width_um,
            (index + 1) * pdk.canvas_height_um // (len(netlist.outputs) + 1),
        )
        pending.append((name, package_points.get(
            name, (pdk.canvas_width_um - pixel / 2,
                   math.floor(output_y / pixel) * pixel + pixel / 2))))

    # Reserve continuous power trunks first; signal nets can use the alternate
    # routing plane when crossing a rail.
    # Route power and high-fanout/long-span nets first. Scarce channels must be
    # reserved for the nets that are hardest to detour; small local nets can
    # normally use the remaining capacity.
    fanout: dict[str, int] = {}
    for net, _ in pending:
        fanout[net] = fanout.get(net, 0) + 1
    pending.sort(key=lambda item: (
        0 if item[0] in {"$VDD", "$GND"} else 1,
        -fanout[item[0]],
        -(abs(net_sources.get(item[0], item[1])[0] - item[1][0])
          + abs(net_sources.get(item[0], item[1])[1] - item[1][1])),
    ))

    grid_width, grid_height = pdk.image_width_px, pdk.image_height_px
    blocked: set[tuple[int, int]] = set()
    for x1, y1, x2, y2 in cell_boxes:
        for gx in range(int(x1 / pixel) + 1, int(x2 / pixel) - 1):
            for gy in range(int(y1 / pixel) + 1, int(y2 / pixel) - 1):
                blocked.add((gx, gy))
    # Metal2 is preferred. Higher planes are overflow capacity and are only
    # occupied when all lower legal planes fail, keeping ordinary designs
    # compressed to the smallest practical layer count.
    routing_layers = tuple(
        f"metal{index}" for index in range(2, 25)
        if f"metal{index}" in pdk.layers
    )
    occupied_by_layer: dict[str, dict[tuple[int, int], str]] = {
        layer: {} for layer in routing_layers
    }
    occupied_by_level: dict[int, dict[tuple[int, int], str]] = {
        int(layer.removeprefix("metal")): occupied
        for layer, occupied in occupied_by_layer.items()
    }
    active_highest = min(3, max(occupied_by_level))
    for route_index, (net, destination) in enumerate(pending):
        source = net_sources.get(net)
        if not source:
            unrouted.append(f"{net} -> ({destination[0]},{destination[1]})")
            continue
        start_grid = (int(source[0] // pixel), int(source[1] // pixel))
        goal_grid = (int(destination[0] // pixel), int(destination[1] // pixel))
        clearance = max(1, math.ceil(pdk.min_spacing_um / pixel))

        path3d = None
        chosen_start = (start_grid[0], start_grid[1], 2)
        chosen_source = source
        # Activate exactly one additional layer only after all paths through
        # the currently planned stack have failed the exhaustive 3-D search.
        for trial_highest in range(active_highest, max(occupied_by_level) + 1):
            same_net_states = [
                (point[0], point[1], level)
                for level in range(2, trial_highest + 1)
                for point, owner in occupied_by_level[level].items()
                if owner == net
            ]
            if same_net_states:
                chosen_start = min(
                    same_net_states,
                    key=lambda state: (
                        abs(state[0] - goal_grid[0])
                        + abs(state[1] - goal_grid[1])
                        + abs(state[2] - 2) * pdk.via_penalty
                    ),
                )
                chosen_source = (
                    (chosen_start[0] + 0.5) * pixel,
                    (chosen_start[1] + 0.5) * pixel,
                )
            else:
                chosen_start = (start_grid[0], start_grid[1], 2)
                chosen_source = source
            path3d = _astar_multilayer(
                chosen_start, (goal_grid[0], goal_grid[1], 2),
                grid_width, grid_height, trial_highest,
                occupied_by_level, net, clearance,
                pdk.via_penalty, pdk.coupling_penalty,
                pdk.interlayer_offset_p,
            )
            if path3d is not None:
                active_highest = max(active_highest, trial_highest)
                break
        if path3d is None:
            unrouted.append(f"{net}: no p-grid multilayer path from {source} to {destination}")
            continue

        current_level = path3d[0][2]
        segment = [(path3d[0][0], path3d[0][1])]
        for previous, state in zip(path3d, path3d[1:]):
            x, y, level = state
            if level == current_level:
                segment.append((x, y))
                continue
            shapes.extend(_path_rects(segment, pixel, net, f"metal{current_level}"))
            via_level = min(current_level, level)
            shapes.append(Rect(
                f"via{via_level}{via_level + 1}",
                x * pixel, y * pixel, (x + 1) * pixel, (y + 1) * pixel, net,
            ))
            occupied_by_level[current_level][(x, y)] = net
            occupied_by_level[level][(x, y)] = net
            current_level = level
            segment = [(x, y)]
        shapes.extend(_path_rects(segment, pixel, net, f"metal{current_level}"))

        # Both external endpoints are metal1 landings. A branch beginning on
        # an existing same-net tree already owns its connection.
        if chosen_source == source:
            sx, sy = start_grid
            shapes.append(Rect("via12", sx * pixel, sy * pixel,
                               (sx + 1) * pixel, (sy + 1) * pixel, net))
        gx, gy = goal_grid
        shapes.append(Rect("via12", gx * pixel, gy * pixel,
                           (gx + 1) * pixel, (gy + 1) * pixel, net))
        for x, y, level in path3d:
            occupied_by_level[level][(x, y)] = net
        routes.append((net, chosen_source, destination))

    # The greedy tree router may revisit a branch endpoint. One physical via
    # at one coordinate is sufficient for a net, so remove exact duplicates
    # before DRC, mask export and parasitic extraction.
    unique_shapes: list[Rect] = []
    seen_vias: set[tuple[str, float, float, float, float, str]] = set()
    for shape in shapes:
        if shape.layer.startswith("via"):
            key = (shape.layer, shape.x1, shape.y1, shape.x2, shape.y2, shape.label)
            if key in seen_vias:
                continue
            seen_vias.add(key)
        unique_shapes.append(shape)
    shapes = unique_shapes

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


def layout_layer_names(layout: Layout) -> set[str]:
    return {shape.layer for shape in layout.shapes}


def _route_is_connected(layout: Layout, net: str, source: tuple[int, int], sink: tuple[int, int]) -> bool:
    conductors = {layer for layer in layout_layer_names(layout) if layer.startswith(("metal", "via"))}
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
    pads = [shape for shape in layout.shapes
            if shape.layer == "metal1" and shape.label.startswith("PIN")]
    for index, first in enumerate(pads):
        for second in pads[index + 1:]:
            overlap_x = min(first.x2, second.x2) - max(first.x1, second.x1)
            overlap_y = min(first.y2, second.y2) - max(first.y1, second.y1)
            if overlap_x > epsilon and overlap_y > epsilon:
                errors.append(f"E_PAD_OVERLAP {first.label} overlaps {second.label}")
    for net, source, sink in layout.routes:
        if not _route_is_connected(layout, net, source, sink):
            errors.append(f"E_CONNECT {net}: {source} does not reach {sink}")
    route_nets = {net for net, _, _ in layout.routes}
    route_layers = {layer for layer in known_layers if layer.startswith(("metal", "via"))}
    route_shapes = [s for s in layout.shapes if s.layer in route_layers and s.label in route_nets]
    for index, first in enumerate(route_shapes):
        for second in route_shapes[index + 1:]:
            if first.layer == second.layer and first.label != second.label and _touches(first, second):
                errors.append(f"E_SHORT {first.label} touches {second.label}")
    return errors
