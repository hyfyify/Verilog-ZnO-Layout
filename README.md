# Verilog ZnO Layout

## ZnO Verilog physical/electrical descriptors

Descriptors may be placed before the Verilog module; see
`examples/package_voltage_demo.txt`. `PIN=n` is a physical package pin. Under
this project's DIP convention, pin 1 is upper-right, numbering proceeds around
the body, and upper-left is the final pin. Compilation rejects illegal,
duplicate or required-but-missing pins, incompatible logic levels, unpowered
voltage domains, illegal boost paths and unsafe voltage adaptation. Upward
crossings insert a physical `LEVEL_SHIFTER_UP`. Downward crossings require a
measured PDK `Ids(Vgs,Vds)` bias-driver model; without one they fail safely.

An experimental, measurement-driven compiler that converts synthesizable Verilog into mask layers for an additive ZnO electrolyte-gated TFT process.

> **Current status:** runnable MVP. The included PDK values and transistor drawings are conservative placeholders, not fabrication-qualified rules.

## What works

- Uses Yosys for synthesis when it is installed.
- Falls back to a small parser for combinational `assign` expressions using `~`, `&`, `|`, and `^`.
- Maps gates to parameterized ZnO TFT cell drawings.
- Reads the Verilog/Yosys-to-TFT mapping directly from `pdk/cells.json` instead of hard-coding it in the layout engine.
- Places cells and creates simple Manhattan metal routing, including top-level output pads.
- Uses the measured pixel-grid TFT template: red 5p×2p ZnO, grey 2p×1p S/D, blue 3p×1p central electrolyte, and grey 1p×1p gate; S/G/D centres are offset by 2p.
- Adds visible internal silver rails so external input/output routes terminate on cell conductors.
- Expands AND/OR/BUF/XOR/XNOR into cascadable weak-load NOT/NAND/NOR NMOS cells before placement.
- Uses four-neighbour A* on the native LCoS pixel grid for shortest Manhattan routes; diagonal wiring is impossible.
- Routes cell-local conductors on `metal1` and global nets across `metal2`, `metal3`, and overflow `metal4`; `via12`, `via23`, and `via34` are emitted as separate fabrication masks. Crossings on different metal planes are legal, while same-plane shorts remain fatal.
- Maximizes equal cell spacing in each row and fails DRC when a route cannot be completed.
- Routes each primitive cell's VDD and GND terminals as checked global power nets.
- Supports `voltage` and nonvolatile `rram` Verilog-ZnO descriptors. Capacity is exactly one bit per ZnO layer: X/ZnO/Y stores 1 bit per stack; the X/ZnO/Y/ZnO/X “burger” stores 2 bits per stack and emits distinct upper/lower masks.
- Checks minimum feature width, canvas bounds, missing drivers, and source-to-sink route continuity.
- Exports an interactive SVG preview, JSON placed netlist, DRC text report, and one 1280×960 monochrome PNG per process layer.
- Uses only the Python standard library at runtime.

This release is intended to validate the software pipeline. It does **not** yet model depletion, fan-out, voltage drop, overlay tolerance, shorts between unrelated nets, or fabricate-ready transistor topology.

## Quick start

Python 3.10 or newer is required. Yosys is recommended but optional for the included examples. Source files may use `.v` or `.txt`; the compiler reads their contents rather than requiring an extension.

Verilog-ZnO extensions are written before the module and removed before standard Verilog synthesis:

```verilog
voltage core(vdd=0.8, high=0.5, low=0.1);
rram config[31:0](stack="X/ZnO/Y", read=0.1, set=2.0, reset=-2.0);
```

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


## Mobile web compiler

The touch-first web interface can load a `.v`, `.sv` or `.txt` file, compile it,
show the interactive SVG, DRC report and placed netlist, and download the SVG/report.

On a Windows PC, expose it to a phone on the same Wi-Fi:

```powershell
zno-layout-web --host 0.0.0.0 --port 8000
ipconfig
```

Open `http://<PC IPv4 address>:8000` on the phone. Keep the terminal open and
allow Python through Windows Firewall on a private network. Compilation runs on
the PC while the complete UI is controlled by the phone.

Android can also compile locally in Termux (fallback Verilog parser; Yosys is
optional and normally unavailable):

```bash
pkg update
pkg install python git
git clone https://github.com/hyfyify/Verilog-ZnO-Layout.git
cd Verilog-ZnO-Layout
python -m pip install -e .
zno-layout-web --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000` in the same phone. Large ALU routing may take
several minutes, so keep Termux and the browser alive.

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
