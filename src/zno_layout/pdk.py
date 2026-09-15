from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PDK:
    pixel_pitch_um: float
    tft_length_p: int
    tft_width_p: int
    grid_um: float
    min_width_um: float
    device_width_um: float
    device_length_um: float
    min_spacing_um: float
    cell_width_um: float
    cell_height_um: float
    row_spacing_um: float
    canvas_width_um: int
    canvas_height_um: int
    image_width_px: int
    image_height_px: int
    layers: tuple[str, ...]
    colors: dict[str, str]
    cells: dict[str, dict]
    rram_cell_width_p: int
    rram_cell_height_p: int
    rram_bits_per_zno: int
    wire_capacitance_ff_per_p: float
    via_capacitance_ff: float
    coupling_penalty: int
    interlayer_offset_p: int
    max_net_capacitance_ff: float
    max_bus_skew_p: int

    @classmethod
    def load(cls, path: str | Path) -> "PDK":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        pitch = float(data.get("pixel_pitch_um", data["grid_um"]))
        width_p = int(data.get("tft_width_p", 2))
        length_p = int(data.get("tft_length_p", 7))
        cell_path = Path(path).parent / data.get("cell_library", "cells.json")
        cells = json.loads(cell_path.read_text(encoding="utf-8"))
        return cls(
            pixel_pitch_um=pitch,
            tft_length_p=length_p,
            tft_width_p=width_p,
            grid_um=data["grid_um"],
            min_width_um=data["min_width_um"],
            device_width_um=data.get("device_width_um", pitch * width_p),
            device_length_um=data.get("device_length_um", pitch * length_p),
            min_spacing_um=data["min_spacing_um"],
            cell_width_um=data["cell_width_um"],
            cell_height_um=data["cell_height_um"],
            row_spacing_um=data["row_spacing_um"],
            canvas_width_um=data["canvas_width_um"],
            canvas_height_um=data["canvas_height_um"],
            image_width_px=data["image_width_px"],
            image_height_px=data["image_height_px"],
            layers=tuple(data["layers"]),
            colors=data["colors"],
            cells=cells,
            rram_cell_width_p=int(data.get("rram_cell_width_p", 3)),
            rram_cell_height_p=int(data.get("rram_cell_height_p", 3)),
            rram_bits_per_zno=int(data.get("rram_bits_per_zno", 1)),
            wire_capacitance_ff_per_p=float(data.get("wire_capacitance_ff_per_p", 0.02)),
            via_capacitance_ff=float(data.get("via_capacitance_ff", 0.05)),
            coupling_penalty=int(data.get("coupling_penalty", 6)),
            interlayer_offset_p=int(data.get("interlayer_offset_p", 2)),
            max_net_capacitance_ff=float(data.get("max_net_capacitance_ff", 500.0)),
            max_bus_skew_p=int(data.get("max_bus_skew_p", 5000)),
        )
