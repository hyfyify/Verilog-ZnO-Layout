import json
import math
import tempfile
import unittest
from pathlib import Path

from zno_layout.cli import compile_design
from zno_layout.extensions import parse_extensions
from zno_layout.electrical import SynthesisError, parse_electrical, validate_and_adapt
from zno_layout.layout import _astar_grid, _cell_shapes, _touches, place_and_route, run_drc
from zno_layout.layout import _rram_shapes
from zno_layout.model import Gate
from zno_layout.pdk import PDK
from zno_layout.physical import analyze_physical, physical_drc, scan_routing_layers
from zno_layout.techmap import expand_to_nmos_primitives, prune_unused_logic
from zno_layout.verilog import parse_assign_verilog, run_yosys


ROOT = Path(__file__).parents[1]


class PipelineTests(unittest.TestCase):
    def test_fallback_parser(self):
        design = parse_assign_verilog("module m(input wire a, input wire b, output wire y); assign y = ~(a & b); endmodule")
        self.assertEqual(design.inputs, ["a", "b"])
        self.assertEqual(design.outputs, ["y"])
        self.assertEqual([gate.kind for gate in design.gates], ["AND", "NOT"])
        self.assertEqual(design.gates[-1].output, "y")

    def test_expression_temporaries_do_not_alias_output(self):
        design = parse_assign_verilog(
            "module m(input wire a, input wire b, input wire c, output wire y); "
            "assign y = a | (b & c); endmodule"
        )
        self.assertEqual(design.gates[0].output, "$tmp1")
        self.assertEqual(design.gates[1].inputs, ["a", "$tmp1"])
        self.assertEqual(design.gates[1].output, "y")

    def test_default_pdk(self):
        pdk = PDK.load(ROOT / "pdk/default.json")
        self.assertEqual((pdk.image_width_px, pdk.image_height_px), (1280, 960))
        self.assertIn("zno", pdk.layers)
        self.assertEqual(pdk.tft_length_p, 5)
        self.assertEqual(pdk.tft_width_p, 2)
        self.assertEqual(pdk.cells["NAND"]["tft_count"], 3)
        self.assertEqual(pdk.rram_bits_per_zno, 1)
        self.assertGreater(pdk.layer_activation_penalty, pdk.via_penalty)
        self.assertEqual(pdk.interlayer_offset_p, 2)

    def test_compile_creates_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            result = compile_design(ROOT / "examples/inverter_chain.v", output, ROOT / "pdk/default.json")
            self.assertEqual(result, 0)
            self.assertTrue((output / "layout.svg").exists())
            self.assertTrue((output / "mask_zno.png").exists())
            self.assertFalse((output / "mask_metal24.png").exists())
            parsed = json.loads((output / "netlist.json").read_text())
            self.assertEqual(len(parsed["gates"]), 3)

    def test_every_sink_and_output_is_routed(self):
        design = parse_assign_verilog(
            "module m(input wire a, input wire b, output wire y); assign y = ~(a & b); endmodule"
        )
        pdk = PDK.load(ROOT / "pdk/default.json")
        layout = place_and_route(expand_to_nmos_primitives(design), pdk)
        self.assertEqual(layout.unrouted, [])
        self.assertEqual(len(layout.routes), 5)  # two inputs, output, VDD and GND for one NAND cell
        self.assertEqual(run_drc(layout, pdk), [])

    def test_missing_driver_is_a_drc_error(self):
        design = parse_assign_verilog(
            "module m(input wire a, output wire y); assign y = a & missing; endmodule"
        )
        pdk = PDK.load(ROOT / "pdk/default.json")
        errors = run_drc(place_and_route(expand_to_nmos_primitives(design), pdk), pdk)
        self.assertTrue(any(error.startswith("E_UNROUTED missing") for error in errors))

    def test_tft_matches_five_by_two_pixel_cross(self):
        design = parse_assign_verilog(
            "module m(input wire a, output wire y); assign y = ~a; endmodule"
        )
        pdk = PDK.load(ROOT / "pdk/default.json")
        layout = place_and_route(design, pdk)
        zno = [s for s in layout.shapes if s.layer == "zno"]
        sd = [s for s in layout.shapes if s.layer == "source_drain"]
        electrolyte = [s for s in layout.shapes if s.layer == "electrolyte"]
        # Gate electrodes are 1p×1p; longer shapes on this layer are insulated
        # gate-control traces and are checked separately for grid/shorts.
        gate_names = {item.name for item in design.gates}
        gate = [s for s in layout.shapes if s.layer == "gate" and s.label in gate_names]
        self.assertTrue(all(math.isclose(s.width, 2 * pdk.pixel_pitch_um, abs_tol=1e-6) for s in zno))
        self.assertTrue(all(math.isclose(s.height, pdk.device_length_um, abs_tol=1e-6) for s in zno))
        self.assertTrue(all(math.isclose(s.width, 2 * pdk.pixel_pitch_um, abs_tol=1e-6) and math.isclose(s.height, pdk.pixel_pitch_um, abs_tol=1e-6) for s in sd))
        self.assertTrue(all(math.isclose(s.width, 3 * pdk.pixel_pitch_um, abs_tol=1e-6) and math.isclose(s.height, pdk.pixel_pitch_um, abs_tol=1e-6) for s in electrolyte))
        self.assertTrue(all(math.isclose(s.width, pdk.pixel_pitch_um, abs_tol=1e-6) and math.isclose(s.height, pdk.pixel_pitch_um, abs_tol=1e-6) for s in gate))

    def test_cell_pins_touch_visible_metal(self):
        pdk = PDK.load(ROOT / "pdk/default.json")
        shapes, pins = _cell_shapes(Gate("NOT", "inv", ["a"], "y", 100, 100), pdk)
        metal = [shape for shape in shapes if shape.layer == "metal1"]
        for point in pins.values():
            self.assertTrue(any(
                shape.x1 <= point[0] <= shape.x2 and shape.y1 <= point[1] <= shape.y2
                for shape in metal
            ))

    def test_txt_extension_content(self):
        source = (ROOT / "examples/verilog.txt").read_text(encoding="utf-8")
        design = parse_assign_verilog(source)
        self.assertEqual(design.name, "text_input_demo")
        self.assertEqual(design.gates[-1].output, "y")

    def test_voltage_and_rram_descriptors(self):
        source = (ROOT / "examples/rram_demo.txt").read_text(encoding="utf-8")
        cleaned, voltages, rrams = parse_extensions(source)
        self.assertNotIn("voltage core", cleaned)
        self.assertNotIn("rram config", cleaned)
        self.assertEqual((voltages[0].vdd, voltages[0].high, voltages[0].low), (0.8, 0.5, 0.1))
        self.assertEqual(rrams[0].bits, 32)
        self.assertEqual(rrams[0].stack, ("X", "ZnO", "Y"))

    def test_rram_compiles_to_three_mask_layers(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            result = compile_design(ROOT / "examples/rram_demo.txt", output, ROOT / "pdk/default.json")
            self.assertEqual(result, 0)
            for layer in ("rram_x", "rram_zno", "rram_y"):
                self.assertTrue((output / f"mask_{layer}.png").exists())
            netlist = json.loads((output / "netlist.json").read_text())
            self.assertEqual(netlist["rram"][0]["bits"], 32)

    def test_one_bit_per_zno_rram_cell(self):
        source = ROOT / "examples/rram_demo.txt"
        from zno_layout.verilog import run_yosys
        design = run_yosys(source)
        pdk = PDK.load(ROOT / "pdk/default.json")
        shapes, boxes = _rram_shapes(design, pdk)
        self.assertEqual(len(boxes), 32)
        self.assertEqual(len([shape for shape in shapes if shape.layer == "rram_zno"]), 32)

    def test_burger_has_two_zno_bits_per_stack(self):
        from zno_layout.verilog import run_yosys
        design = run_yosys(ROOT / "examples/rram_burger_demo.txt")
        pdk = PDK.load(ROOT / "pdk/default.json")
        shapes, boxes = _rram_shapes(design, pdk)
        self.assertEqual(len(boxes), 16)
        self.assertEqual(len([s for s in shapes if s.layer == "rram_zno_bottom"]), 16)
        self.assertEqual(len([s for s in shapes if s.layer == "rram_zno_top"]), 16)

    def test_boolean_gate_expands_to_real_nmos_cells(self):
        design = parse_assign_verilog(
            "module m(input wire a, input wire b, output wire y); assign y = a & b; endmodule"
        )
        mapped = expand_to_nmos_primitives(design)
        self.assertEqual([gate.kind for gate in mapped.gates], ["NAND", "NOT"])

    def test_inverted_and_collapses_directly_to_nand(self):
        design = parse_assign_verilog(
            "module m(input wire a, input wire b, output wire y); assign y = ~(a & b); endmodule"
        )
        mapped = expand_to_nmos_primitives(design)
        self.assertEqual([(gate.kind, gate.output) for gate in mapped.gates], [("NAND", "y")])

    def test_nand_has_series_pull_down_network(self):
        pdk = PDK.load(ROOT / "pdk/default.json")
        network = pdk.cells["NAND"]["transistor_network"]
        self.assertEqual(network[1]["S"], "N1")
        self.assertEqual(network[2]["D"], "N1")
        self.assertNotEqual(network[1]["G"], network[2]["G"])

    def test_astar_returns_shortest_orthogonal_path(self):
        path = _astar_grid((0, 0), (5, 3), 10, 10, set(), {}, "n")
        self.assertIsNotNone(path)
        self.assertEqual(len(path) - 1, 8)
        self.assertTrue(all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in zip(path, path[1:])))


    def test_adjacent_layer_overlap_is_a_soft_routing_penalty(self):
        direct = _astar_grid((0, 2), (6, 2), 8, 6, set(), {}, "n")
        offset = _astar_grid(
            (0, 2), (6, 2), 8, 6, set(), {}, "n",
            soft_occupied={(x, 2) for x in range(1, 6)},
            soft_radius=2, soft_penalty=8,
        )
        self.assertEqual(len(direct) - 1, 6)
        self.assertTrue(any(y != 2 for _, y in offset[1:-1]))

    def test_physical_metrics_cover_bus_capacitance_and_core_area(self):
        pdk = PDK.load(ROOT / "pdk/default.json")
        design = expand_to_nmos_primitives(parse_assign_verilog(
            "module m(input wire A0, input wire A1, output wire Y0, output wire Y1); "
            "assign Y0=~A0; assign Y1=~A1; endmodule"
        ))
        layout = place_and_route(design, pdk)
        metrics = analyze_physical(layout, design, pdk)
        self.assertGreater(metrics.core_area_um2, 0)
        self.assertGreater(metrics.total_wire_length_p, 0)
        self.assertIn("Y", metrics.bus_skew_p)
        self.assertTrue(metrics.net_capacitance_ff)
        self.assertGreater(metrics.routing_cost, metrics.total_wire_length_p)
        self.assertTrue(metrics.layer_utilization)
        self.assertEqual(physical_drc(metrics, pdk), [])
        self.assertEqual(scan_routing_layers(layout, pdk), [])

    def test_all_metal_edges_are_on_pixel_grid(self):
        pdk = PDK.load(ROOT / "pdk/default.json")
        design = expand_to_nmos_primitives(parse_assign_verilog(
            "module m(input wire a, input wire b, output wire y); assign y = ~(a & b); endmodule"
        ))
        layout = place_and_route(design, pdk)
        for shape in (shape for shape in layout.shapes if shape.layer == "metal1"):
            for coordinate in (shape.x1, shape.y1, shape.x2, shape.y2):
                units = coordinate / pdk.pixel_pitch_um
                self.assertTrue(math.isclose(units, round(units), abs_tol=1e-6))

    def test_dip_pin_order_and_level_shifter(self):
        from zno_layout.verilog import run_yosys
        design = run_yosys(ROOT / "examples/package_voltage_demo.txt")
        validate_and_adapt(design, PDK.load(ROOT / "pdk/default.json"))
        self.assertEqual(design.package.pin_position(1), ("RIGHT", 0))
        self.assertEqual(design.package.pin_position(14), ("LEFT", 0))
        self.assertEqual(design.electrical_components[0].kind, "LEVEL_SHIFTER_UP")

    def test_illegal_package_pin_fails(self):
        source = 'package DIP14(); pin a(PIN=15, TYPE=INPUT, V_RANGE="0~0.8V");'
        _, package, pins, rails, drives = parse_electrical(source)
        design = parse_assign_verilog("module m(input wire a, output wire y); assign y=a; endmodule")
        design.package, design.pin_descriptors = package, pins
        with self.assertRaisesRegex(SynthesisError, "E_PIN_RANGE"):
            validate_and_adapt(design, PDK.load(ROOT / "pdk/default.json"))

    def test_illegal_unpowered_boost_fails(self):
        source = ('package DIP14(); pin VDD(PIN=1, TYPE=POWER, V_RANGE="0.8~0.8V", NOMINAL=0.8V); '
                  'pin a(PIN=2, TYPE=INPUT, V_RANGE="0~0.8V"); '
                  'pin y(PIN=3, TYPE=OUTPUT, V_RANGE="0~0.8V"); '
                  'power HV(VOLTAGE=2V, SOURCE=VDD, PRIMITIVE=REGULATOR);')
        _, package, pins, rails, drives = parse_electrical(source)
        design = parse_assign_verilog("module m(input wire a, output wire y); assign y=a; endmodule")
        design.package, design.pin_descriptors, design.power_rails = package, pins, rails
        with self.assertRaisesRegex(SynthesisError, "E_POWER_BOOST"):
            validate_and_adapt(design, PDK.load(ROOT / "pdk/default.json"))

    def test_full_adder_routes_on_multiple_metal_layers(self):
        from zno_layout.verilog import run_yosys
        pdk = PDK.load(ROOT / "pdk/default.json")
        design = expand_to_nmos_primitives(run_yosys(ROOT / "examples/full_adder.txt"))
        layout = place_and_route(design, pdk)
        self.assertEqual(run_drc(layout, pdk), [])
        used = {shape.layer for shape in layout.shapes}
        self.assertIn("metal2", used)
        highest = max(int(layer.removeprefix("metal"))
                      for layer in used if layer.startswith("metal"))
        self.assertLessEqual(highest, 24)

    def test_nor_gate_inputs_do_not_short_output_or_ground(self):
        from zno_layout.verilog import run_yosys
        pdk = PDK.load(ROOT / "pdk/default.json")
        design = expand_to_nmos_primitives(run_yosys(ROOT / "examples/full_adder.txt"))
        layout = place_and_route(design, pdk)
        nor = next(gate for gate in design.gates if gate.kind == "NOR")
        relevant = {nor.output, *nor.inputs, "$GND", "$VDD"}
        shapes = [s for s in layout.shapes if s.layer == "metal1" and s.label in relevant]
        shorts = [(a.label, b.label) for index, a in enumerate(shapes)
                  for b in shapes[index + 1:] if a.label != b.label and _touches(a, b)]
        self.assertEqual(shorts, [])



    def test_mobile_web_ui_is_packaged_for_touch_compilation(self):
        from zno_layout.web import WEB_ROOT
        page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('name="viewport"', page)
        self.assertIn('/api/compile', page)
        self.assertIn('accept=".v,.sv,.txt,text/plain"', page)
        self.assertIn('pinch-zoom', page)

    def test_unused_logic_is_absent_from_tft_layout(self):
        design = parse_assign_verilog(
            "module m(input wire a, input wire b, output wire y); "
            "wire dead; assign dead = a & b; assign y = ~a; endmodule"
        )
        before = len(design.gates)
        prune_unused_logic(design)
        self.assertLess(len(design.gates), before)
        self.assertNotIn("dead", {gate.output for gate in design.gates})
        mapped = expand_to_nmos_primitives(design)
        self.assertFalse(any(
            gate.output == "dead" or "dead" in gate.inputs
            for gate in mapped.gates
        ))
        pdk = PDK.load(ROOT / "pdk/default.json")
        layout = place_and_route(mapped, pdk)
        self.assertFalse(any(shape.label == "dead" for shape in layout.shapes))
        self.assertEqual(run_drc(layout, pdk), [])

    def test_die_pin_zero_and_alu8_stress(self):
        pdk = PDK.load(ROOT / "pdk/default.json")
        design = run_yosys(ROOT / "examples/alu4_8op.txt")
        validate_and_adapt(design, pdk)
        self.assertEqual(design.package.name, "DIE24")
        self.assertEqual(design.package.pin_position(0), ("PERIMETER", 0))
        mapped = expand_to_nmos_primitives(design)
        layout = place_and_route(mapped, pdk)
        pin0 = layout.pins["A0"]
        self.assertTrue(math.isclose(pin0[0], pdk.canvas_width_um / 2 + pdk.pixel_pitch_um / 2,
                                     abs_tol=pdk.pixel_pitch_um))
        self.assertTrue(math.isclose(pin0[1], pdk.pixel_pitch_um / 2,
                                     abs_tol=1e-6))
        self.assertEqual(run_drc(layout, pdk), [])


if __name__ == "__main__":
    unittest.main()
