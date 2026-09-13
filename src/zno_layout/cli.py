from __future__ import annotations

import argparse
from pathlib import Path

from .export import write_masks, write_netlist, write_svg
from .layout import place_and_route, run_drc
from .pdk import PDK
from .verilog import run_yosys


def compile_design(source: Path, output: Path, pdk_path: Path, top: str | None = None) -> int:
    pdk = PDK.load(pdk_path)
    netlist = run_yosys(source, top)
    layout = place_and_route(netlist, pdk)
    errors = run_drc(layout, pdk)
    output.mkdir(parents=True, exist_ok=True)
    write_svg(layout, pdk, output / "layout.svg")
    write_masks(layout, pdk, output)
    write_netlist(netlist, output / "netlist.json")
    report = ["ZnO Layout DRC", "================", f"Gate count: {layout.gate_count}"]
    report += [f"Errors: {len(errors)}", ""] + (errors or ["PASS"])
    (output / "drc_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Compiled {netlist.name}: {len(netlist.gates)} gates, {len(errors)} DRC errors")
    print(f"Output: {output.resolve()}")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile Verilog into experimental ZnO mask layers")
    parser.add_argument("source", type=Path, help="Verilog source file")
    parser.add_argument("-o", "--output", type=Path, default=Path("build"))
    parser.add_argument("--pdk", type=Path, default=Path("pdk/default.json"))
    parser.add_argument("--top")
    args = parser.parse_args(argv)
    return compile_design(args.source, args.output, args.pdk, args.top)


if __name__ == "__main__":
    raise SystemExit(main())

