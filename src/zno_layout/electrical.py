from __future__ import annotations

import math
import re

from .model import (DriveRequest, ElectricalComponent, Gate, Netlist,
                    PackageDescriptor, PinDescriptor, PowerRail)


class SynthesisError(ValueError):
    pass


def _v(value: str) -> float:
    return float(value.strip().upper().removesuffix("V"))


def _range(value: str) -> tuple[float, float]:
    parts = re.split(r"\s*(?:~|\.\.)\s*", value.strip().upper().removesuffix("V"))
    if len(parts) == 1:
        number = _v(parts[0])
        return number, number
    return _v(parts[0]), _v(parts[1])


def _args(text: str) -> dict[str, str]:
    result = {}
    for item in re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", text):
        if "=" in item:
            key, value = item.split("=", 1)
            result[key.strip().upper()] = value.strip().strip('"')
    return result


PACKAGE = re.compile(r"\bpackage\s+(DIP|PLCC|DIE)(\d+)\s*\((.*?)\)\s*;", re.I | re.S)
PIN = re.compile(r"\bpin\s+(\w+)\s*\((.*?)\)\s*;", re.I | re.S)
POWER = re.compile(r"\bpower\s+(\w+)\s*\((.*?)\)\s*;", re.I | re.S)
DRIVE = re.compile(r"\bdrive\s+(\w+)\s*->\s*(\w+)\s*\((.*?)\)\s*;", re.I | re.S)


def parse_electrical(source: str):
    package = None
    pins, rails, drives = [], [], []
    match = PACKAGE.search(source)
    if match:
        args = _args(match.group(3))
        count = int(match.group(2))
        required = tuple(int(x) for x in re.findall(r"\d+", args.get("REQUIRED", "")))
        package = PackageDescriptor(f"{match.group(1).upper()}{count}", count, required)
    for match in PIN.finditer(source):
        args = _args(match.group(2))
        missing = {"PIN", "TYPE", "V_RANGE"} - args.keys()
        if missing:
            raise SynthesisError(f"E_PIN_DESCRIPTOR {match.group(1)} missing {','.join(sorted(missing))}")
        kind = args["TYPE"].upper()
        if kind not in {"INPUT", "OUTPUT", "INOUT", "POWER", "GND"}:
            raise SynthesisError(f"E_PIN_TYPE {match.group(1)} has illegal TYPE={kind}")
        logic0 = _range(args["LOGIC0"]) if "LOGIC0" in args else None
        logic1 = _range(args["LOGIC1"]) if "LOGIC1" in args else None
        pins.append(PinDescriptor(match.group(1), int(args["PIN"]), kind, *_range(args["V_RANGE"]),
                                  logic0, logic1, _v(args["NOMINAL"]) if "NOMINAL" in args else None,
                                  args.get("REQUIRED", "TRUE").upper() != "FALSE"))
    for match in POWER.finditer(source):
        args = _args(match.group(2))
        rails.append(PowerRail(match.group(1), _v(args["VOLTAGE"]), args["SOURCE"],
                               args.get("PRIMITIVE", "DIRECT").upper(),
                               args.get("BOOST", "FALSE").upper() == "TRUE"))
    for match in DRIVE.finditer(source):
        args = _args(match.group(3))
        drives.append(DriveRequest(match.group(1), match.group(2), _range(args["SOURCE_LOW"]),
                                   _range(args["SOURCE_HIGH"]), _range(args["TARGET_LOW"]),
                                   _range(args["TARGET_HIGH"]), args.get("HIGH_RAIL", "")))
    cleaned = PACKAGE.sub("", PIN.sub("", POWER.sub("", DRIVE.sub("", source))))
    return cleaned, package, pins, rails, drives


def validate_and_adapt(netlist: Netlist, pdk) -> None:
    package, pins = netlist.package, netlist.pin_descriptors
    if pins and package is None:
        raise SynthesisError("E_PACKAGE_MISSING pin descriptors require a package")
    if package:
        seen = set()
        for pin in pins:
            first_pin = 0 if package.name.startswith("DIE") else 1
            last_pin = package.pin_count - 1 if first_pin == 0 else package.pin_count
            if not first_pin <= pin.number <= last_pin:
                raise SynthesisError(f"E_PIN_RANGE {package.name} cannot use PIN={pin.number}")
            if pin.number in seen:
                raise SynthesisError(f"E_PIN_DUPLICATE PIN={pin.number}")
            seen.add(pin.number)
            if pin.v_min > pin.v_max:
                raise SynthesisError(f"E_PIN_VOLTAGE {pin.signal} has reversed V_RANGE")
            for level in (pin.logic0, pin.logic1):
                if level and not (pin.v_min <= level[0] <= level[1] <= pin.v_max):
                    raise SynthesisError(f"E_PIN_VOLTAGE {pin.signal} logic level exceeds V_RANGE")
        missing = set(package.required_pins) - seen
        if missing:
            raise SynthesisError(f"E_PIN_REQUIRED missing physical pins {sorted(missing)}")
        described = {p.signal for p in pins}
        missing_io = set(netlist.inputs + netlist.outputs) - described
        if missing_io:
            raise SynthesisError(f"E_PIN_REQUIRED missing signal pins {sorted(missing_io)}")

    pin_by_signal = {p.signal: p for p in pins}
    external_power = {p.signal: (p.nominal if p.nominal is not None else p.v_max)
                      for p in pins if p.kind == "POWER"}
    resolved = dict(external_power)
    pending = list(netlist.power_rails)
    while pending:
        progressed = False
        for rail in pending[:]:
            if rail.source not in resolved:
                continue
            source_v = resolved[rail.source]
            if rail.voltage > source_v + 1e-9 and not (rail.boost or rail.primitive in {"BOOST", "CHARGE_PUMP"}):
                raise SynthesisError(f"E_POWER_BOOST {rail.source}={source_v}V cannot create {rail.name}={rail.voltage}V")
            resolved[rail.name] = rail.voltage
            pending.remove(rail)
            progressed = True
        if not progressed:
            names = ", ".join(r.name for r in pending)
            raise SynthesisError(f"E_POWER_PATH no external POWER path for {names}")
    for domain in netlist.voltage_domains:
        if not any(math.isclose(v, domain.vdd, abs_tol=0.03) for v in resolved.values()):
            raise SynthesisError(f"E_POWER_PATH voltage domain {domain.name} requires {domain.vdd}V")

    models = pdk.cells
    for index, request in enumerate(netlist.drive_requests, 1):
        source_high, target_high = request.source_high[1], request.target_high[1]
        direct = (max(request.source_low[0], request.target_low[0]) <=
                  min(request.source_low[1], request.target_low[1]) and
                  max(request.source_high[0], request.target_high[0]) <=
                  min(request.source_high[1], request.target_high[1]))
        if direct:
            # A direct-compatible drive is a net alias, not a physical TFT.
            # Rewrite every logical sink so the routed source reaches it.
            for gate in netlist.gates:
                gate.inputs = [request.source if item == request.target else item
                               for item in gate.inputs]
            netlist.electrical_components.append(ElectricalComponent(
                "DIRECT", f"direct_{index}", request.source, request.target))
        elif target_high > source_high:
            if request.high_rail not in resolved or resolved[request.high_rail] < target_high:
                raise SynthesisError(f"E_LEVEL_SHIFTER_POWER {request.target} needs {target_high}V rail")
            name = f"auto_ls_up_{index}"
            netlist.electrical_components.append(ElectricalComponent(
                "LEVEL_SHIFTER_UP", name, request.source, request.target, request.high_rail))
            netlist.gates.append(Gate("LEVEL_SHIFTER_UP", name, [request.source], request.target))
        elif target_high < source_high:
            if not models.get("BIAS_DRIVER", {}).get("measured_ids_model"):
                raise SynthesisError("E_DIVIDER_MODEL no measured Ids(Vgs,Vds) model; unsafe TFT divider rejected")
            name = f"auto_bias_{index}"
            netlist.electrical_components.append(ElectricalComponent(
                "BIAS_DRIVER", name, request.source, request.target, request.high_rail))
            netlist.gates.append(Gate("BIAS_DRIVER", name, [request.source], request.target))
        else:
            raise SynthesisError(f"E_LOGIC_LEVEL {request.source} cannot directly drive {request.target}")

    absolute_max = float(models.get("TFT", {}).get("v_abs_max", math.inf))
    if any(p.v_max > absolute_max for p in pins if p.kind not in {"POWER", "GND"}):
        raise SynthesisError(f"E_TFT_ABS signal exceeds TFT absolute maximum {absolute_max}V")
    limits = models.get("TFT", {})
    for request in netlist.drive_requests:
        vgs = request.target_high[1] - request.target_low[0]
        if vgs > float(limits.get("vgs_max", 999)):
            raise SynthesisError(f"E_VGS_MAX {request.target} may reach VGS={vgs}V")
