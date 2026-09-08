"""WP-17b/WP-49: the backend synthesizes the recorded integration top and never guesses one."""
from __future__ import annotations

import inspect
import json

from orchestrator.langgraph import backend_graph as bg
from orchestrator.langgraph.backend_graph import _recorded_integration_top


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


def test_record_without_module_name_is_not_a_top(tmp_path):
    d = tmp_path / "rtl" / "integration"
    d.mkdir(parents=True)
    (d / "t.v").write_text("module t(); endmodule\n")
    (tmp_path / ".coresmith").mkdir()
    (tmp_path / ".coresmith" / "integration_result.json").write_text(json.dumps({
        "top_rtl_path": str(d / "t.v")}))
    assert _recorded_integration_top(tmp_path) == ("", "")


def test_backend_never_guesses_from_files():
    assert not hasattr(bg, "_file_top_module") and not hasattr(bg, "_select_integration_top")
    src = inspect.getsource(bg.init_design_node)
    assert "does not" in src and "guess" in src
