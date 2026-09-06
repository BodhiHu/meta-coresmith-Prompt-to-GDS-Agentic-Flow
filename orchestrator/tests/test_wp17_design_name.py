"""WP-17: the integration design name comes from the real top module."""
from __future__ import annotations

import json

from orchestrator.langgraph.integration_helpers import (
    _existing_top_module,
    load_architecture_connections,
)

TOP = "h264_enc_core_top"


def _write_top(int_dir, name, body):
    int_dir.mkdir(parents=True, exist_ok=True)
    (int_dir / name).write_text(body)


def test_top_behind_same_line_comment_beats_helper(tmp_path):
    body = (
        "module h264_normative_rom_arbiter(input clk); endmodule\n"
        "/* Design: x */ module " + TOP + "(input clk); "
        "h264_normative_rom_arbiter u_arb(.clk(clk)); endmodule\n"
    )
    _write_top(tmp_path / "rtl" / "integration", TOP + ".v", body)
    assert _existing_top_module(tmp_path / "rtl" / "integration") == TOP


def test_preferred_name_wins_when_declared(tmp_path):
    body = "module helper(); endmodule\nmodule " + TOP + "(); helper h(); endmodule\n"
    _write_top(tmp_path / "rtl" / "integration", "chip.v", body)
    assert _existing_top_module(tmp_path / "rtl" / "integration", TOP) == TOP
    assert _existing_top_module(tmp_path / "rtl" / "integration", "other") == TOP


def test_no_file_returns_empty(tmp_path):
    assert _existing_top_module(tmp_path / "rtl" / "integration") == ""


def test_load_architecture_connections_uses_real_top(tmp_path):
    cs = tmp_path / ".coresmith"
    cs.mkdir()
    (cs / "architecture_state.json").write_text(json.dumps({
        "block_diagram": {"connections": [{"from_block": "a", "to_block": "b"}]},
        "prd_spec": {"prd": {"title": "PRD - H264 Enc Core"}},
    }))
    conns, name = load_architecture_connections(str(tmp_path))
    assert conns and name == "h264_enc_core_top"
    body = (
        "module rom_arbiter(input clk); endmodule\n"
        "/* c */ module h264_enc_core_top(input clk); rom_arbiter u(.clk(clk)); endmodule\n"
    )
    _write_top(tmp_path / "rtl" / "integration", "h264_enc_core_top.v", body)
    conns, name = load_architecture_connections(str(tmp_path))
    assert name == "h264_enc_core_top"
