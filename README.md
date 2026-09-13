# Verilog ZnO Layout

An experimental, measurement-driven compiler that converts synthesizable Verilog into mask layers for an additive ZnO electrolyte-gated TFT process.

> **Current status:** runnable MVP. The included PDK values and transistor drawings are conservative placeholders, not fabrication-qualified rules.

## What works

- Uses Yosys for synthesis when it is installed.
- Falls back to a small parser for combinational `assign` expressions using `~`, `&`, `|`, and `^`.
- Maps gates to parameterized ZnO TFT cell drawings.
- Places cells and creates simple Manhattan metal routing.
- Checks minimum feature width and canvas bounds.
- Exports an interactive SVG preview, JSON placed netlist, DRC text report, and one 1280×960 monochrome PNG per process layer.
- Uses only the Python standard library at runtime.

This release is intended to validate the software pipeline. It does **not** yet model depletion, fan-out, voltage drop, overlay tolerance, shorts between unrelated nets, or fabricate-ready transistor topology.

## Quick start

Python 3.10 or newer is required. Yosys is recommended but optional for the included examples.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux: source .venv/bin/activate
python -m pip install -e .
zno-layout examples/inverter_chain.v -o build/inverter_chain
```

Open `build/inverter_chain/layout.svg` in a browser. The exposure masks are written as:

```text
mask_zno.png
mask_source_drain.png
mask_electrolyte.png
mask_gate.png
mask_metal1.png
```

Run the tests:

```bash
python -m unittest discover -s tests -v
```

## PDK

All dimensions in `pdk/default.json` are micrometres. The default canvas is 8128×6096 µm and maps to 1280×960 pixels (6.35 µm/pixel), matching the current 0.4-inch LCoS target.

Before fabrication, replace these provisional values with measured values:

- minimum printable width and spacing;
- ZnO, Ag, electrolyte, and gate overlap;
- alignment tolerance between exposures;
- valid layer contacts and forbidden material combinations;
- transistor dimensions, drive strength, fan-out, and required buffers;
- mask polarity and optical correction.

## Supported fallback Verilog

Without Yosys, use a single ANSI-style module with scalar ports and continuous assignments:

```verilog
module example(input wire a, input wire b, output wire y);
    assign y = ~(a & b);
endmodule
```

Install Yosys to support broader synthesizable Verilog. Unsupported synthesized cell types deliberately stop compilation instead of silently generating a wrong layout.

## Roadmap

1. Replace symbolic cells with measured INV/NAND/NOR physical cells.
2. Add connectivity extraction and LVS comparison against the synthesized netlist.
3. Add spacing, overlap, short-circuit, and alignment-aware DRC.
4. Add net-aware multi-layer routing and buffer insertion.
5. Add GDSII export through KLayout or `gdstk`.
6. Add sequential cells and RRAM configuration switches.
7. Build a visual editor for cell inspection and manual routing correction.

## Safety

Generated masks are experimental. Inspect every layer, run test coupons first, and verify electrical behavior before scaling to large circuits.
