from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Gate:
    kind: str
    name: str
    inputs: list[str]
    output: str
    x: int = 0
    y: int = 0


@dataclass
class Netlist:
    name: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)


@dataclass(frozen=True)
class Rect:
    layer: str
    x1: int
    y1: int
    x2: int
    y2: int
    label: str = ""

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1


@dataclass
class Layout:
    width: int
    height: int
    shapes: list[Rect]
    pins: dict[str, tuple[int, int]]
    gate_count: int
    routes: list[tuple[str, tuple[int, int], tuple[int, int]]] = field(default_factory=list)
    unrouted: list[str] = field(default_factory=list)
