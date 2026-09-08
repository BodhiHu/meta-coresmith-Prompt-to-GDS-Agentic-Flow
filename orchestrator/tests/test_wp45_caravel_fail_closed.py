"""WP-45: preprocessor-aware port parsing; a locked Caravel boundary fails closed."""
from __future__ import annotations

import inspect

from orchestrator.langgraph import pipeline_graph as pg
from orchestrator.langgraph.contract_conformance import declared_ports, strip_preprocessor
from orchestrator.langgraph.integration_helpers import parse_verilog_ports

PAD = """
module user_project_wrapper (
`ifdef USE_POWER_PINS
    inout  wire         vdda1,
    inout  wire         vssd1,
`endif
    input  wire         wb_clk_i,
`ifndef SKIP_RST
    input  wire         wb_rst_i,
`else
    input  wire         other_rst,
`endif
    input  wire [37:0]  io_in,
    output wire [37:0]  io_out,
    output wire [37:0]  io_oeb
);
endmodule
"""


def test_strip_preprocessor_sees_the_no_define_configuration():
    out = strip_preprocessor(PAD)
    assert "vdda1" not in out and "`" not in out
    assert "wb_rst_i" in out and "other_rst" not in out
    with_pins = strip_preprocessor(PAD, defines={"USE_POWER_PINS", "SKIP_RST"})
    assert "vdda1" in with_pins and "other_rst" in with_pins and "wb_rst_i" not in with_pins


def test_nested_conditionals():
    src = "`ifdef A\na\n`ifdef B\nb\n`else\nnb\n`endif\n`elsif C\nc\n`else\nz\n`endif\n"
    assert strip_preprocessor(src).split() == ["z"]
    assert strip_preprocessor(src, {"A"}).split() == ["a", "nb"]
    assert strip_preprocessor(src, {"A", "B"}).split() == ["a", "b"]
    assert strip_preprocessor(src, {"C"}).split() == ["c"]


def test_parsers_never_see_endif_as_a_port(tmp_path):
    f = tmp_path / "user_project_wrapper.v"
    f.write_text(PAD)
    names = {p.name for p in parse_verilog_ports(str(f)).ports}
    assert "endif" not in names and "vdda1" not in names
    assert {"wb_clk_i", "wb_rst_i", "io_in", "io_out", "io_oeb"} <= names
    dp = declared_ports(PAD)
    assert "endif" not in dp and "vdda1" not in dp and "io_oeb" in dp


def test_locked_boundary_parks_instead_of_llm_fallback():
    src = inspect.getsource(pg.integration_check_node)
    assert src.count("_park_caravel_assembly_failure(") == 2
    assert "escalating to Integration Lead / fail-closed interrupt" not in src
    park = inspect.getsource(pg._park_caravel_assembly_failure)
    assert '"phase": "caravel_assembly"' in park and '["retry", "fix_rtl", "abort"]' in park
