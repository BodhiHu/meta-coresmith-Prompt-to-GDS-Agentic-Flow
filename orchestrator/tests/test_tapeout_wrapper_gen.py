# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Fast regression tests for the OpenFrame wrapper generation helpers.

Pins the fixes for: comment/multi-name port parsing, clk/rst port selection
(exact wins, substring is a fallback), fully-driven io_out/io_oeb and the
power-connection module's declared rail.
"""

from __future__ import annotations

import re
import textwrap

from orchestrator.langgraph.tapeout_helpers import (
    OPENFRAME_IO_PADS,
    _discover_block_ports,
    _generate_power_connection,
    _generate_wrapper_verilog,
    _parse_verilog_ports,
)


class TestParseVerilogPortsRobustness:
    def test_comments_are_not_ports(self):
        rtl = textwrap.dedent("""\
            // input valid from the arbiter
            /* output done_flag */
            module m (
                input  wire clk,
                output wire q
            );
            endmodule
        """)
        ports = _parse_verilog_ports(rtl)
        assert set(ports) == {"clk", "q"}

    def test_multi_name_declaration(self):
        ports = _parse_verilog_ports("module m; input [7:0] cfg_a, cfg_b; endmodule\n")
        assert ports["cfg_a"]["width"] == 8
        assert ports["cfg_b"] == {"width": 8, "direction": "input"}

    def test_ansi_list_stops_at_next_direction(self):
        ports = _parse_verilog_ports(
            "module m (input wire a, output wire [3:0] q); endmodule\n")
        assert set(ports) == {"a", "q"}
        assert ports["q"]["direction"] == "output"


class TestClkPortSelection:
    def test_exact_clk_beats_substring_and_is_not_gpio_mapped(self, tmp_path, monkeypatch):
        import orchestrator.langgraph.pipeline_helpers as ph
        import orchestrator.langgraph.tapeout_helpers as th

        monkeypatch.setattr(th, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(ph, "PROJECT_ROOT", tmp_path)
        rtl_dir = tmp_path / "rtl" / "my_block"
        rtl_dir.mkdir(parents=True)
        (rtl_dir / "my_block.v").write_text(
            "module my_block (input wire clk, input wire rst_n, "
            "output wire pclk_out); endmodule\n"
        )
        (tmp_path / ".coresmith").mkdir(exist_ok=True)

        enriched = _discover_block_ports([{"name": "my_block"}])
        assert enriched[0]["_clk_port"] == "clk"
        # pclk_out is a normal output pad, not the clock
        assert "pclk_out" in enriched[0]["ports"]


class TestGeneratedWrapper:
    def _wrapper(self, tmp_path):
        blocks = [{"name": "blk", "_clk_port": "clk", "_rst_port": "rst_n", "ports": {}}]
        gpio = {"blk": {
            "q": {"start": 2, "width": 2, "direction": "output"},
            "d": {"start": 4, "width": 1, "direction": "input"},
        }}
        return _generate_wrapper_verilog(blocks, gpio, tmp_path).read_text()

    def test_every_pad_drives_io_out_and_io_oeb(self, tmp_path):
        src = self._wrapper(tmp_path)
        for i in range(OPENFRAME_IO_PADS):
            assert re.search(rf"io_oeb\[{i}\] = 1'b[01];", src), f"io_oeb[{i}] undriven"
        # pads 2..3 are driven by the block output, everything else is tied low
        for i in list(range(0, 2)) + list(range(4, OPENFRAME_IO_PADS)):
            assert f"assign io_out[{i}] = 1'b0;" in src, f"io_out[{i}] undriven"

    def test_no_duplicate_pad_assignment(self, tmp_path):
        src = self._wrapper(tmp_path)
        for i in (0, 1, 2, 3):
            assert src.count(f"assign io_oeb[{i}] =") == 1


class TestPowerConnection:
    def test_internal_rail_is_declared(self, tmp_path):
        src = _generate_power_connection(tmp_path, "vccd1", "VPWR").read_text()
        assert "inout wire VPWR" in src
        assert "assign VPWR = vccd1;" in src


class TestRomResponderLanes:
    def test_quad_rejected_loudly(self):
        import pytest

        from orchestrator.langgraph.bfm_lib import QSPIRomContract, QSPIRomResponderBFM

        with pytest.raises(ValueError, match="lanes=1"):
            QSPIRomResponderBFM(object(), QSPIRomContract(lanes=4), b"\x00")
