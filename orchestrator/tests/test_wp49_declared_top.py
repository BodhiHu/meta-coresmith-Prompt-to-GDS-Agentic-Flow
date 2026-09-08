"""WP-49: one declared top module, one candidate receipt, no guessing."""
from __future__ import annotations

import inspect
import json

from orchestrator.harness import top_module as tm
from orchestrator.langgraph import pipeline_graph as pg
from orchestrator.langgraph.integration_helpers import lint_top_level  # noqa: F401


def test_declared_top_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("CORESMITH_TOP_MODULE", raising=False)
    assert tm.declared_top(tmp_path) == ""
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "task.yaml").write_text("chassis: x\ntop: my_locked_top   # comment\nclock: clk\n")
    assert tm.declared_top(tmp_path) == "my_locked_top"
    monkeypatch.setenv("CORESMITH_TOP_MODULE", "env_top")
    assert tm.declared_top(tmp_path) == "env_top"


def test_module_declared_in_sees_lint_configuration(tmp_path):
    f = tmp_path / "a.v"
    f.write_text("// module fake\n`ifdef NEVER\nmodule hidden(); endmodule\n`endif\nmodule real_top(); endmodule\n")
    assert tm.module_declared_in(f, "real_top")
    assert not tm.module_declared_in(f, "fake") and not tm.module_declared_in(f, "hidden")


def test_receipt_roundtrip_and_resolution(tmp_path):
    (tmp_path / "rtl").mkdir()
    top = tmp_path / "rtl" / "top.v"
    top.write_text("module chip(); leaf u(); endmodule\n")
    leaf = tmp_path / "rtl" / "leaf.v"
    leaf.write_text("module leaf(); endmodule\n")
    r = tm.write_candidate_receipt(tmp_path, "chip", str(top), {"leaf": str(leaf)}, note="t")
    assert r["top_module"] == "chip" and len(r["sources"]) == 2 and len(r["candidate_sha"]) == 64
    assert tm.resolve_top(tmp_path) == ("chip", str(top.resolve()))
    leaf.write_text("module leaf(input x); endmodule\n")
    r2 = tm.write_candidate_receipt(tmp_path, "chip", str(top), {"leaf": str(leaf)})
    assert r2["candidate_sha"] != r["candidate_sha"]
    try:
        tm.write_candidate_receipt(tmp_path, "not_there", str(top), {})
        assert False, "a receipt must not lie about its top"
    except ValueError:
        pass


def test_resolve_top_falls_back_to_a_verified_integration_record(tmp_path):
    (tmp_path / ".coresmith").mkdir()
    (tmp_path / "rtl").mkdir()
    top = tmp_path / "rtl" / "t.v"
    top.write_text("module real_one(); endmodule\n")
    (tmp_path / ".coresmith" / "integration_result.json").write_text(json.dumps(
        {"top_module": "wrong_name", "top_rtl_path": str(top)}))
    assert tm.resolve_top(tmp_path) == ("", "")
    (tmp_path / ".coresmith" / "integration_result.json").write_text(json.dumps(
        {"top_module": "real_one", "top_rtl_path": str(top)}))
    assert tm.resolve_top(tmp_path) == ("real_one", str(top))


def test_integration_check_records_and_enforces_the_top():
    src = inspect.getsource(pg.integration_check_node)
    assert src.count("write_candidate_receipt(") >= 3   # caravel, single-block, lead paths
    assert '"top module mismatch"' in src
    assert "_self_assembled_wrapper" not in src


def test_lint_uses_an_explicit_top():
    src = inspect.getsource(lint_top_level)
    assert 'top_module or Path(top_rtl_path).stem' in src
