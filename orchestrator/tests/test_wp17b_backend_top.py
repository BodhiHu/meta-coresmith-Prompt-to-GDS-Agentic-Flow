"""WP-17b: the backend synthesizes the integration top the frontend DV'd."""
from __future__ import annotations

import json

from orchestrator.langgraph.backend_graph import (
    _file_top_module,
    _recorded_integration_top,
    _select_integration_top,
)


def test_file_top_module_ignores_same_line_comment_and_helper():
    src = ("module rom_arbiter(input clk); endmodule\n"
           "/* Design: x */ module chip_core_top(input clk); rom_arbiter u(.clk(clk)); endmodule\n")
    assert _file_top_module(src) == "chip_core_top"


def test_select_prefers_real_top_over_helper_first_file(tmp_path):
    d = tmp_path / "rtl" / "integration"
    d.mkdir(parents=True)
    (d / "a_old_top.v").write_text(
        "module helper(input clk); endmodule\n"
        "/* c */ module old_top(input clk); helper h(.clk(clk)); endmodule\n")
    (d / "helper.v").write_text(
        "module helper(input clk); endmodule\n"
        "module new_top(input clk); helper h(.clk(clk)); endmodule\n")
    f, m = _select_integration_top(d)
    assert m in ("old_top", "new_top")  # both are roots; heuristic stays deterministic
    assert f.endswith(".v")


def test_recorded_top_wins(tmp_path):
    d = tmp_path / "rtl" / "integration"
    d.mkdir(parents=True)
    (d / "new.v").write_text("module new_top(input clk); endmodule\n")
    (tmp_path / ".coresmith").mkdir()
    (tmp_path / ".coresmith" / "integration_result.json").write_text(json.dumps({
        "top_rtl_path": str(d / "new.v"), "top_module": "new_top"}))
    assert _recorded_integration_top(tmp_path) == (str(d / "new.v"), "new_top")


def test_recorded_top_missing_file_is_ignored(tmp_path):
    (tmp_path / ".coresmith").mkdir()
    (tmp_path / ".coresmith" / "integration_result.json").write_text(json.dumps({
        "top_rtl_path": str(tmp_path / "gone.v"), "top_module": "x"}))
    assert _recorded_integration_top(tmp_path) == ("", "")
