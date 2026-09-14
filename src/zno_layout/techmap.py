from __future__ import annotations

from .model import Gate, Netlist



def prune_unused_logic(netlist: Netlist) -> Netlist:
    """Remove gates that cannot affect a declared output or required driver.

    This is a conservative backwards reachability pass. RRAM descriptors,
    package pins, power rails and electrical components are metadata/physical
    side effects and are retained unchanged.
    """
    required = set(netlist.outputs)
    required.update(component.output_net for component in netlist.electrical_components)
    kept_reversed: list[Gate] = []
    for gate in reversed(netlist.gates):
        if gate.output in required:
            kept_reversed.append(gate)
            required.update(gate.inputs)
    netlist.gates = list(reversed(kept_reversed))
    return netlist


def expand_to_nmos_primitives(netlist: Netlist) -> Netlist:
    """Expand Boolean gates into cascadable NOT/NAND/NOR weak-load NMOS cells."""
    result: list[Gate] = []
    serial = 0

    def add(kind: str, inputs: list[str], output: str) -> str:
        nonlocal serial
        serial += 1
        result.append(Gate(kind, f"tcell{serial}_{kind.lower()}", inputs, output))
        return output

    def temporary(base: str, suffix: str) -> str:
        return f"${base}_{suffix}"

    drivers = {gate.output: gate for gate in netlist.gates}
    use_count: dict[str, int] = {}
    for gate in netlist.gates:
        for net in gate.inputs:
            use_count[net] = use_count.get(net, 0) + 1
    collapsed_source: dict[str, tuple[str, str]] = {}
    skipped_not: set[str] = set()
    inverse = {"AND": "NAND", "OR": "NOR", "XOR": "XNOR"}
    for gate in netlist.gates:
        if gate.kind == "NOT" and gate.inputs:
            source = drivers.get(gate.inputs[0])
            if source and source.kind in inverse and use_count.get(source.output) == 1:
                collapsed_source[source.name] = (inverse[source.kind], gate.output)
                skipped_not.add(gate.name)

    for gate in netlist.gates:
        if gate.name in skipped_not:
            continue
        if gate.name in collapsed_source:
            kind, output = collapsed_source[gate.name]
            gate = Gate(kind, gate.name, gate.inputs, output)
        a = gate.inputs[0]
        b = gate.inputs[1] if len(gate.inputs) > 1 else None
        if gate.kind in {"NOT", "NAND", "NOR", "LEVEL_SHIFTER_UP", "BIAS_DRIVER"}:
            add(gate.kind, gate.inputs, gate.output)
        elif gate.kind == "BUF":
            mid = temporary(gate.name, "inv")
            add("NOT", [a], mid)
            add("NOT", [mid], gate.output)
        elif gate.kind == "AND":
            mid = temporary(gate.name, "nand")
            add("NAND", [a, b], mid)
            add("NOT", [mid], gate.output)
        elif gate.kind == "OR":
            mid = temporary(gate.name, "nor")
            add("NOR", [a, b], mid)
            add("NOT", [mid], gate.output)
        elif gate.kind in {"XOR", "XNOR"}:
            n1 = temporary(gate.name, "xor_n1")
            n2 = temporary(gate.name, "xor_n2")
            n3 = temporary(gate.name, "xor_n3")
            xor_out = gate.output if gate.kind == "XOR" else temporary(gate.name, "xor")
            add("NAND", [a, b], n1)
            add("NAND", [a, n1], n2)
            add("NAND", [b, n1], n3)
            add("NAND", [n2, n3], xor_out)
            if gate.kind == "XNOR":
                add("NOT", [xor_out], gate.output)
        else:
            raise ValueError(f"No NMOS primitive expansion for {gate.kind}")
    return Netlist(
        name=netlist.name, inputs=list(netlist.inputs), outputs=list(netlist.outputs), gates=result,
        voltage_domains=list(netlist.voltage_domains), rrams=list(netlist.rrams),
        package=netlist.package, pin_descriptors=list(netlist.pin_descriptors),
        power_rails=list(netlist.power_rails), drive_requests=list(netlist.drive_requests),
        electrical_components=list(netlist.electrical_components),
    )
