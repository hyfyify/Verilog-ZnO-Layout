from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PDK:
    grid_um: int
    min_width_um: int
    min_spacing_um: int
    cell_width_um: int
    cell_height_um: int
    row_spacing_um: int
    canvas_width_um: int
    canvas_height_um: int
    image_width_px: int
    image_height_px: int
    layers: tuple[str, ...]
    colors: dict[str, str]

    @classmethod
    def load(cls, path: str | Path) -> "PDK":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            grid_um=data["grid_um"],
            min_width_um=data["min_width_um"],
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
        )

