"""WP-17b/WP-49: the backend synthesizes the recorded integration top and never guesses one."""
from __future__ import annotations

import json

import pytest

from orchestrator.harness.top_module import CandidateError
from orchestrator.langgraph.backend_graph import _recorded_integration_top
from orchestrator.tests.candidate_fixtures import adopt


def test_recorded_top_wins(tmp_path):
    d = tmp_path / "rtl" / "integration"
    d.mkdir(parents=True)
    (d / "new.v").write_text("module new_top(input clk); endmodule\n")
    (tmp_path / ".coresmith").mkdir()
    (tmp_path / ".coresmith" / "integration_result.json").write_text(json.dumps({
        "top_rtl_path": str(d / "new.v"), "top_module": "new_top"}))
    adopt(tmp_path, d / "new.v", name="new_top")
    assert _recorded_integration_top(tmp_path) == (str(d / "new.v"), "new_top")


def test_recorded_top_missing_file_is_ignored(tmp_path):
    (tmp_path / ".coresmith").mkdir()
    (tmp_path / ".coresmith" / "integration_result.json").write_text(json.dumps({
        "top_rtl_path": str(tmp_path / "gone.v"), "top_module": "x"}))
    with pytest.raises(CandidateError):
        _recorded_integration_top(tmp_path)


def test_record_without_module_name_is_not_a_top(tmp_path):
    d = tmp_path / "rtl" / "integration"
    d.mkdir(parents=True)
    (d / "t.v").write_text("module t(); endmodule\n")
    (tmp_path / ".coresmith").mkdir()
    (tmp_path / ".coresmith" / "integration_result.json").write_text(json.dumps({
        "top_rtl_path": str(d / "t.v")}))
    with pytest.raises(CandidateError):
        _recorded_integration_top(tmp_path)
