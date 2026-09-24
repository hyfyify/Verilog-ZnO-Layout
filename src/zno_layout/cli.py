from __future__ import annotations

import argparse
from pathlib import Path

from .export import write_masks, write_netlist, write_svg
from .layout import place_and_route, run_drc
from .pdk import PDK
from .physical import analyze_physical, physical_drc, scan_routing_layers
from .techmap import expand_to_nmos_primitives, prune_unused_logic
from .verilog import run_yosys
from .electrical import SynthesisError, validate_and_adapt


def compile_design(source: Path, output: Path, pdk_path: Path, top: str | None = None) -> int:
    pdk = PDK.load(pdk_path)
    logical = run_yosys(source, top)
    validate_and_adapt(logical, pdk)
    prune_unused_logic(logical)
    netlist = expand_to_nmos_primitives(logical)
    layout = place_and_route(netlist, pdk)
    errors = run_drc(layout, pdk)
    metrics = analyze_physical(layout, netlist, pdk)
    errors.extend(physical_drc(metrics, pdk))
    errors.extend(scan_routing_layers(layout, pdk))
    output.mkdir(parents=True, exist_ok=True)
    # Never leave a stale successful layout behind after a failed rebuild.
    for stale in [output / "layout.svg", *output.glob("mask_*.png")]:
        stale.unlink(missing_ok=True)
    write_netlist(netlist, output / "netlist.json")
    used_metals = sorted(
        {shape.layer for shape in layout.shapes if shape.layer.startswith("metal")},
        key=lambda name: int(name.removeprefix("metal")),
    )
    highest_metal = used_metals[-1] if used_metals else "none"
    report = [
        "ZnO Layout DRC", "================",
        f"Gate count: {layout.gate_count}",
        f"Routing layers used: {', '.join(used_metals) or 'none'}",
        f"Highest routing layer: {highest_metal}",
        f"Core area: {metrics.core_area_um2:.2f} um^2",
        f"Total wire length: {metrics.total_wire_length_p}p",
        f"Via count: {metrics.via_count}",
        f"Adjacent-layer overlap: {metrics.adjacent_layer_overlap_p}p",
        f"Weighted routing cost: {metrics.routing_cost:.0f}",
        "Layer self-scan utilization: " + (", ".join(
            f"{layer}={value * 100:.2f}%"
            for layer, value in sorted(
                metrics.layer_utilization.items(),
                key=lambda item: int(item[0].removeprefix("metal")),
            )
        ) or "none"),
        "Bus skew: " + (", ".join(
            f"{bus}={skew}p" for bus, skew in sorted(metrics.bus_skew_p.items())
        ) or "none"),
        "Largest estimated capacitances: " + (", ".join(
            f"{net}={cap:.3f}fF"
            for net, cap in sorted(
                metrics.net_capacitance_ff.items(), key=lambda item: item[1], reverse=True
            )[:10]
        ) or "none"),
    ]
    report += [f"Errors: {len(errors)}", ""] + (errors or ["PASS"])
    (output / "drc_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    if not errors:
        write_svg(layout, pdk, output / "layout.svg")
        write_masks(layout, pdk, output)
    print(f"Compiled {netlist.name}: {len(netlist.gates)} gates, {len(errors)} DRC errors")
    print(f"Output: {output.resolve()}")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile Verilog into experimental ZnO mask layers")
    parser.add_argument("source", type=Path, help="Verilog source (.v or .txt; extension is unrestricted)")
    parser.add_argument("-o", "--output", type=Path, default=Path("build"))
    parser.add_argument("--pdk", type=Path, default=Path("pdk/default.json"))
    parser.add_argument("--top")
    args = parser.parse_args(argv)
    try:
        return compile_design(args.source, args.output, args.pdk, args.top)
    except SynthesisError as error:
        print(f"SYNTHESIS FAILED: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
