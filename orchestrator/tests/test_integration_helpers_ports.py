# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Port parsing + top-level wiring regressions for integration_helpers."""

from __future__ import annotations

from pathlib import Path

from orchestrator.langgraph import integration_helpers
from orchestrator.langgraph.integration_helpers import (
    VerilogModule,
    VerilogPort,
    generate_top_level_rtl,
    parse_verilog_ports,
)

GROUPED = """
module grouped_block (
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] coeff_re, coeff_im,
    output reg  [15:0] acc_a, acc_b
);
endmodule
"""


def test_grouped_ansi_declaration_keeps_every_port(tmp_path: Path):
    p = tmp_path / "grouped_block.v"
    p.write_text(GROUPED, encoding="utf-8")
    mod = parse_verilog_ports(str(p))
    names = {x.name: x for x in mod.ports}
    assert set(names) == {"clk", "rst_n", "coeff_re", "coeff_im",
                          "acc_a", "acc_b"}
    assert names["coeff_im"].direction == "input"
    assert names["coeff_im"].width == 8
    assert names["acc_b"].direction == "output"
    assert names["acc_b"].width == 16


def _mod(name: str, ports: list[VerilogPort]) -> VerilogModule:
    return VerilogModule(name=name, ports=ports)


def test_fanout_source_drives_every_consumer_wire(tmp_path, monkeypatch):
    # One producer output feeding TWO consumers: the instantiation binds only
    # the first wire, so the second must be driven by an assign.
    monkeypatch.setattr(integration_helpers, "PROJECT_ROOT", tmp_path)
    modules = {
        "prod": _mod("prod", [VerilogPort("clk", "input"),
                              VerilogPort("dout", "output", width=8, msb=7)]),
        "c1": _mod("c1", [VerilogPort("clk", "input"),
                          VerilogPort("din", "input", width=8, msb=7)]),
        "c2": _mod("c2", [VerilogPort("clk", "input"),
                          VerilogPort("din", "input", width=8, msb=7)]),
    }
    conns = [
        {"from_block": "prod", "to_block": "c1",
         "from_port": "dout", "to_port": "din", "interface": "d1"},
        {"from_block": "prod", "to_block": "c2",
         "from_port": "dout", "to_port": "din", "interface": "d2"},
    ]
    v = generate_top_level_rtl("fanout_top", conns, modules)["verilog"]
    w1 = "w_prod_dout_to_c1_din"
    w2 = "w_prod_dout_to_c2_din"
    assert f".din({w2})" in v
    assert f"assign {w2} = {w1};" in v
