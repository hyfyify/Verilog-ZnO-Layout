from __future__ import annotations

import re
from dataclasses import dataclass

from .model import Layout, Netlist, Rect
from .pdk import PDK


_BUS_BIT = re.compile(r"^(.*?)(\d+)$")


@dataclass(frozen=True)
class PhysicalMetrics:
    core_area_um2: float
    highest_metal: int
    via_count: int
    total_wire_length_p: int
    net_length_p: dict[str, int]
    net_capacitance_ff: dict[str, float]
    bus_skew_p: dict[str, int]
    adjacent_layer_overlap_p: int


def _metal_level(layer: str) -> int:
    return int(layer.removeprefix("metal")) if layer.startswith("metal") else 0


def _wire_length_p(shape: Rect, pixel: float) -> int:
    # A routed rectangle is one pixel wide. Subtract the square landing pixel
    # so joined segments do not double-count every bend.
    return max(1, round(max(shape.width, shape.height) / pixel))


def _projected_overlap_p(a: Rect, b: Rect, pixel: float) -> int:
    x = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    y = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    if not x or not y:
        return 0
    return max(1, round(max(x, y) / pixel))


def analyze_physical(layout: Layout, netlist: Netlist, pdk: PDK) -> PhysicalMetrics:
    metal = [s for s in layout.shapes if s.layer.startswith("metal")]
    vias = [s for s in layout.shapes if s.layer.startswith("via")]
    routed = [s for s in metal if _metal_level(s.layer) >= 2 and s.label]
    net_length: dict[str, int] = {}
    via_per_net: dict[str, int] = {}
    for shape in routed:
        net_length[shape.label] = net_length.get(shape.label, 0) + _wire_length_p(shape, pdk.pixel_pitch_um)
    for shape in vias:
        via_per_net[shape.label] = via_per_net.get(shape.label, 0) + 1

    capacitance = {
        net: round(
            length * pdk.wire_capacitance_ff_per_p
            + via_per_net.get(net, 0) * pdk.via_capacitance_ff,
            4,
        )
        for net, length in net_length.items()
    }

    buses: dict[str, list[str]] = {}
    for net in [*netlist.inputs, *netlist.outputs]:
        match = _BUS_BIT.match(net)
        if match:
            buses.setdefault(match.group(1), []).append(net)
    bus_skew = {}
    for name, bits in buses.items():
        lengths = [net_length.get(bit, 0) for bit in bits]
        if len(lengths) > 1:
            bus_skew[name] = max(lengths) - min(lengths)

    by_level: dict[int, list[Rect]] = {}
    for shape in routed:
        by_level.setdefault(_metal_level(shape.layer), []).append(shape)
    adjacent_overlap = 0
    for level, shapes in by_level.items():
        for first in shapes:
            for second in by_level.get(level + 1, []):
                if first.label != second.label:
                    adjacent_overlap += _projected_overlap_p(first, second, pdk.pixel_pitch_um)

    devices = [s for s in layout.shapes if s.layer == "zno"]
    if devices:
        min_x, max_x = min(s.x1 for s in devices), max(s.x2 for s in devices)
        min_y, max_y = min(s.y1 for s in devices), max(s.y2 for s in devices)
        core_area = (max_x - min_x) * (max_y - min_y)
    else:
        core_area = 0.0

    return PhysicalMetrics(
        core_area_um2=core_area,
        highest_metal=max((_metal_level(s.layer) for s in metal), default=0),
        via_count=len(vias),
        total_wire_length_p=sum(net_length.values()),
        net_length_p=net_length,
        net_capacitance_ff=capacitance,
        bus_skew_p=bus_skew,
        adjacent_layer_overlap_p=adjacent_overlap,
    )


def physical_drc(metrics: PhysicalMetrics, pdk: PDK) -> list[str]:
    errors = [
        f"E_CAP {net}: estimated {capacitance:.3f} fF exceeds {pdk.max_net_capacitance_ff:.3f} fF"
        for net, capacitance in metrics.net_capacitance_ff.items()
        if capacitance > pdk.max_net_capacitance_ff
    ]
    errors.extend(
        f"E_BUS_SKEW {bus}: {skew}p exceeds {pdk.max_bus_skew_p}p"
        for bus, skew in metrics.bus_skew_p.items()
        if skew > pdk.max_bus_skew_p
    )
    return errors
