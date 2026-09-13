from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .model import Gate, Netlist


CELL_KINDS = {
    "$_NOT_": "NOT", "$_AND_": "AND", "$_OR_": "OR", "$_XOR_": "XOR",
    "$_NAND_": "NAND", "$_NOR_": "NOR", "$_XNOR_": "XNOR",
}


def _signal(bit: object, bit_names: dict[int, str]) -> str:
    if isinstance(bit, int):
        return bit_names.get(bit, f"n{bit}")
    return str(bit)


def from_yosys_json(path: str | Path, top: str | None = None) -> Netlist:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    modules = data["modules"]
    top = top or next(iter(modules))
    module = modules[top]
    bit_names: dict[int, str] = {}
    for name, info in module.get("netnames", {}).items():
        for bit in info.get("bits", []):
            if isinstance(bit, int):
                bit_names.setdefault(bit, name)
    inputs, outputs = [], []
    for name, info in module.get("ports", {}).items():
        (inputs if info["direction"] == "input" else outputs).append(name)
    gates: list[Gate] = []
    for name, cell in module.get("cells", {}).items():
        kind = CELL_KINDS.get(cell["type"])
        if not kind:
            raise ValueError(f"Unsupported synthesized cell {cell['type']} ({name})")
        con = cell["connections"]
        out_port = "Y"
        ins = [_signal(con[p][0], bit_names) for p in ("A", "B") if p in con]
        gates.append(Gate(kind, name, ins, _signal(con[out_port][0], bit_names)))
    return Netlist(top, inputs, outputs, gates)


def run_yosys(verilog: str | Path, top: str | None = None) -> Netlist:
    yosys = shutil.which("yosys")
    if not yosys:
        return parse_assign_verilog(Path(verilog).read_text(encoding="utf-8"), top)
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "netlist.json"
        hierarchy = f"hierarchy -top {top};" if top else "hierarchy -auto-top;"
        script = (
            f"read_verilog {Path(verilog).resolve()}; {hierarchy} proc; flatten; opt; "
            f"techmap; opt; abc -g AND,OR,XOR,XNOR,NAND,NOR; clean; write_json {output}"
        )
        result = subprocess.run([yosys, "-q", "-p", script], text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Yosys synthesis failed")
        return from_yosys_json(output, top)


TOKEN = re.compile(r"\s*(~|&|\||\^|\(|\)|[A-Za-z_][A-Za-z0-9_$]*)")
PRECEDENCE = {"|": 1, "^": 2, "&": 3}


def _expression_gates(expr: str, target: str, serial: list[int]) -> list[Gate]:
    tokens = TOKEN.findall(expr)
    position = 0
    gates: list[Gate] = []

    def atom():
        nonlocal position
        if position >= len(tokens):
            raise ValueError("Unexpected end of expression")
        token = tokens[position]
        position += 1
        if token == "~":
            return ("NOT", atom())
        if token == "(":
            value = parse(0)
            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError("Missing closing parenthesis")
            position += 1
            return value
        return token

    def parse(min_prec: int):
        nonlocal position
        left = atom()
        while position < len(tokens) and tokens[position] in PRECEDENCE:
            op = tokens[position]
            prec = PRECEDENCE[op]
            if prec < min_prec:
                break
            position += 1
            right = parse(prec + 1)
            left = ({"&": "AND", "|": "OR", "^": "XOR"}[op], left, right)
        return left

    tree = parse(0)
    if position != len(tokens):
        raise ValueError(f"Cannot parse expression near {tokens[position:]}")

    def emit(node, final: bool = False) -> str:
        if isinstance(node, str):
            if not final:
                return node
            serial[0] += 1
            gates.append(Gate("BUF", f"g{serial[0]}", [node], target))
            return target
        inputs = [emit(child) for child in node[1:]]
        serial[0] += 1
        output = target if final else f"$tmp{serial[0]}"
        gates.append(Gate(node[0], f"g{serial[0]}", inputs, output))
        return output

    emit(tree, final=True)
    return gates


def parse_assign_verilog(source: str, top: str | None = None) -> Netlist:
    source = re.sub(r"//.*?$|/\*.*?\*/", "", source, flags=re.M | re.S)
    match = re.search(r"module\s+(\w+)\s*\((.*?)\)\s*;(.*?)endmodule", source, re.S)
    if not match:
        raise ValueError("Expected one ANSI-style Verilog module")
    name, header, body = match.groups()
    if top and name != top:
        raise ValueError(f"Top module {top!r} not found")
    inputs = re.findall(r"\binput\b(?:\s+wire)?\s+([A-Za-z_]\w*)", header)
    outputs = re.findall(r"\boutput\b(?:\s+wire)?\s+([A-Za-z_]\w*)", header)
    assignments = re.findall(r"assign\s+([A-Za-z_]\w*)\s*=\s*(.*?)\s*;", body, re.S)
    serial = [0]
    gates: list[Gate] = []
    for target, expr in assignments:
        gates.extend(_expression_gates(expr, target, serial))
    if not assignments:
        raise ValueError("Fallback parser requires at least one continuous assign statement")
    return Netlist(name, inputs, outputs, gates)
