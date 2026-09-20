"""WP-46: the Caravel assembler wires by contract, never by a shared bare port name."""
from __future__ import annotations

import re

from orchestrator.langgraph.integration_helpers import (
    generate_caravel_wrapper_top,
    parse_verilog_ports,
)

WRAP = """
module user_project_wrapper (
    input  wire        wb_clk_i,
    input  wire        wb_rst_i,
    input  wire [37:0] io_in,
    output wire [37:0] io_out,
    output wire [37:0] io_oeb
);
  assign io_out = 38'd0;
  assign io_oeb = {38{1'b1}};
endmodule
"""
REGMAP = """
module regmap_buffers (
    input  wire       wb_clk_i,
    input  wire       wb_rst_i,
    output wire       start_valid,
    output wire [6:0] start_length_bytes
);
  assign start_valid = 1'b1;
  assign start_length_bytes = 7'd3;
endmodule
"""
MODEM = """
module modem_controller (
    input  wire       wb_clk_i,
    input  wire       wb_rst_i,
    input  wire       start_valid,
    input  wire [6:0] start_length_bytes,
    output wire       stage_start_valid,
    output wire [6:0] stage_start_length_bytes
);
  assign stage_start_valid = start_valid;
  assign stage_start_length_bytes = start_length_bytes;
endmodule
"""
HDLC = """
module hdlc_framer (
    input  wire       wb_clk_i,
    input  wire       wb_rst_i,
    input  wire       start_valid,
    input  wire [6:0] start_length_bytes
);
endmodule
"""


def _edge(eid, pb, pp, cb, cp, fields):
    return {"edge_id": eid, "producer_block": pb, "producer_port": pp,
            "consumer_block": cb, "consumer_port": cp, "handshake_protocol": "valid_only",
            "fields": [{"name": f} for f in fields], "sideband_signals": []}


def _conn(text: str, inst: str, port: str) -> str:
    body = text[text.index(f"{inst} ("):]
    body = body[:body.index(");")]
    m = re.search(r"\." + re.escape(port) + r"\(([^)]*)\)", body)
    return m.group(1).strip() if m else ""


def _build(tmp_path):
    files = {"user_project_wrapper": WRAP, "regmap_buffers": REGMAP,
             "modem_controller": MODEM, "hdlc_framer": HDLC}
    paths = {}
    for name, src in files.items():
        p = tmp_path / f"{name}.v"
        p.write_text(src)
        paths[name] = str(p)
    modules = {n: parse_verilog_ports(p, n) for n, p in paths.items()}
    return modules, paths


def test_same_named_ports_on_different_blocks_are_not_one_net(tmp_path):
    """The ax25_9600 collision: regmap->modem `start`, modem->hdlc `stage_start`->`start`."""
    modules, paths = _build(tmp_path)
    edges = [_edge("e1", "regmap_buffers", "start", "modem_controller", "start", ["length_bytes"]),
             _edge("e2", "modem_controller", "stage_start", "hdlc_framer", "start",
                   ["stage_start_length_bytes/start_length_bytes"])]
    asm = generate_caravel_wrapper_top(modules, edges, paths, str(tmp_path / "out"),
                                       "user_project_wrapper", None)
    assert asm.get("wiring_errors") in (None, []), asm.get("wiring_errors")
    v = asm["verilog"]
    for sig in ("start_valid", "start_length_bytes"):
        regmap_out = _conn(v, "u_regmap_buffers", sig)
        modem_in = _conn(v, "u_modem_controller", sig)
        modem_out = _conn(v, "u_modem_controller", "stage_" + sig)
        hdlc_in = _conn(v, "u_hdlc_framer", sig)
        assert regmap_out == modem_in and modem_out == hdlc_in, (sig, regmap_out, modem_in, modem_out, hdlc_in)
        assert modem_in != modem_out, f"{sig}: input and output of modem_controller share a net"


def test_two_inputs_are_never_shorted_and_two_drivers_are_a_hazard(tmp_path):
    modules, paths = _build(tmp_path)
    # a legacy (no-signal) edge between two blocks that only share INPUT names
    edges = [{"edge_id": "legacy", "producer_block": "modem_controller", "producer_port": "",
              "consumer_block": "hdlc_framer", "consumer_port": "", "fields": [], "sideband_signals": []}]
    asm = generate_caravel_wrapper_top(modules, edges, paths, str(tmp_path / "out2"),
                                       "user_project_wrapper", None)
    v = asm["verilog"]
    assert _conn(v, "u_modem_controller", "start_valid") != _conn(v, "u_hdlc_framer", "start_valid") or \
        _conn(v, "u_hdlc_framer", "start_valid") in ("", "1'b0")
    # two outputs bound to one consumer input by the contract -> multiply-driven hazard
    edges2 = [_edge("a", "regmap_buffers", "start", "hdlc_framer", "start", ["length_bytes"]),
              _edge("b", "modem_controller", "stage_start", "hdlc_framer", "start",
                    ["stage_start_length_bytes/start_length_bytes"])]
    asm2 = generate_caravel_wrapper_top(modules, edges2, paths, str(tmp_path / "out3"),
                                        "user_project_wrapper", None)
    assert any("multiply-driven" in w for w in (asm2.get("wiring_errors") or [])), asm2.get("wiring_errors")
