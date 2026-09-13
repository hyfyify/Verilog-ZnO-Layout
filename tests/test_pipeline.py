import json
import tempfile
import unittest
from pathlib import Path

from zno_layout.cli import compile_design
from zno_layout.pdk import PDK
from zno_layout.verilog import parse_assign_verilog


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

    def test_compile_creates_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            result = compile_design(ROOT / "examples/inverter_chain.v", output, ROOT / "pdk/default.json")
            self.assertEqual(result, 0)
            self.assertTrue((output / "layout.svg").exists())
            self.assertTrue((output / "mask_zno.png").exists())
            parsed = json.loads((output / "netlist.json").read_text())
            self.assertEqual(len(parsed["gates"]), 3)


if __name__ == "__main__":
    unittest.main()
