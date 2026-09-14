from __future__ import annotations

import re

from .model import RRAMDescriptor, VoltageDomain


VOLTAGE_RE = re.compile(r"\bvoltage\s+(\w+)\s*\((.*?)\)\s*;", re.S)
RRAM_RE = re.compile(r"\brram\s+(\w+)\s*\[(\d+)\s*:\s*(\d+)\]\s*\((.*?)\)\s*;", re.S)


def _arguments(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", text):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        result[key.strip().lower()] = value.strip().strip('"')
    return result


def parse_extensions(source: str) -> tuple[str, list[VoltageDomain], list[RRAMDescriptor]]:
    voltages: list[VoltageDomain] = []
    rrams: list[RRAMDescriptor] = []
    for match in VOLTAGE_RE.finditer(source):
        args = _arguments(match.group(2))
        required = {"vdd", "high", "low"}
        if not required <= args.keys():
            raise ValueError(f"voltage {match.group(1)} requires vdd, high and low")
        domain = VoltageDomain(match.group(1), float(args["vdd"]), float(args["high"]), float(args["low"]))
        if not 0 <= domain.low < domain.high <= domain.vdd:
            raise ValueError(f"invalid thresholds for voltage domain {domain.name}")
        voltages.append(domain)
    for match in RRAM_RE.finditer(source):
        args = _arguments(match.group(4))
        required = {"stack", "read", "set", "reset"}
        if not required <= args.keys():
            raise ValueError(f"rram {match.group(1)} requires stack, read, set and reset")
        stack = tuple(part.strip() for part in args["stack"].split("/"))
        valid_stacks = {("X", "ZnO", "Y"), ("X", "ZnO", "Y", "ZnO", "X")}
        if stack not in valid_stacks:
            raise ValueError(
                f"rram {match.group(1)} requires stack=\"X/ZnO/Y\" or \"X/ZnO/Y/ZnO/X\""
            )
        msb, lsb = int(match.group(2)), int(match.group(3))
        rrams.append(RRAMDescriptor(
            match.group(1), abs(msb - lsb) + 1, stack,
            float(args["read"]), float(args["set"]), float(args["reset"]),
        ))
    cleaned = VOLTAGE_RE.sub("", RRAM_RE.sub("", source))
    return cleaned, voltages, rrams
