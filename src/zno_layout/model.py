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
    voltage_domains: list["VoltageDomain"] = field(default_factory=list)
    rrams: list["RRAMDescriptor"] = field(default_factory=list)
    package: "PackageDescriptor | None" = None
    pin_descriptors: list["PinDescriptor"] = field(default_factory=list)
    power_rails: list["PowerRail"] = field(default_factory=list)
    drive_requests: list["DriveRequest"] = field(default_factory=list)
    electrical_components: list["ElectricalComponent"] = field(default_factory=list)


@dataclass(frozen=True)
class VoltageDomain:
    name: str
    vdd: float
    high: float
    low: float


@dataclass(frozen=True)
class RRAMDescriptor:
    name: str
    bits: int
    stack: tuple[str, ...]
    read_v: float
    set_v: float
    reset_v: float


@dataclass(frozen=True)
class PackageDescriptor:
    name: str
    pin_count: int
    required_pins: tuple[int, ...] = ()

    def pin_position(self, number: int) -> tuple[str, int]:
        """Project convention: DIP pin 1 is upper-right, then clockwise."""
        if self.name.startswith("DIE"):
            return ("PERIMETER", number)
        half = self.pin_count // 2
        if self.name.startswith("DIP"):
            return ("RIGHT", number - 1) if number <= half else ("LEFT", self.pin_count - number)
        quarter = max(1, self.pin_count // 4)
        sides = ("TOP", "RIGHT", "BOTTOM", "LEFT")
        index = number - 1
        return sides[min(3, index // quarter)], index % quarter


@dataclass(frozen=True)
class PinDescriptor:
    signal: str
    number: int
    kind: str
    v_min: float
    v_max: float
    logic0: tuple[float, float] | None = None
    logic1: tuple[float, float] | None = None
    nominal: float | None = None
    required: bool = True


@dataclass(frozen=True)
class PowerRail:
    name: str
    voltage: float
    source: str
    primitive: str = "DIRECT"
    boost: bool = False


@dataclass(frozen=True)
class DriveRequest:
    source: str
    target: str
    source_low: tuple[float, float]
    source_high: tuple[float, float]
    target_low: tuple[float, float]
    target_high: tuple[float, float]
    high_rail: str = ""


@dataclass(frozen=True)
class ElectricalComponent:
    kind: str
    name: str
    input_net: str
    output_net: str
    supply: str = ""


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
