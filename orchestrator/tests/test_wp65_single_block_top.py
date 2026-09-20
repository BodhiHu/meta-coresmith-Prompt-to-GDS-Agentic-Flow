"""WP-65: a declared, already-produced top is the single-block candidate."""
import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from orchestrator.harness import top_module as tm
from orchestrator.harness.hierarchy import elaborate_hierarchy
from orchestrator.langgraph import backend_graph as bg
from orchestrator.langgraph import final_report as fr
from orchestrator.langgraph import integration_helpers as ih
from orchestrator.langgraph import pipeline_graph as pg


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("CORESMITH_TOP_MODULE", raising=False)
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/task.yaml").write_text("chassis: none\ntop: leaf\n")
    rtl = tmp_path / "rtl/implementation.v"
    rtl.parent.mkdir()
    rtl.write_text("module leaf(input a, output y); assign y=a; endmodule\n")
    monkeypatch.setattr(pg, "_current_phase_completed", lambda *a: [{"name": "leaf", "success": True}])
    monkeypatch.setattr(pg, "load_architecture_connections", lambda *a: ([], "Friendly Design"))
    monkeypatch.setattr(pg, "discover_block_rtl", lambda *a: {"leaf": str(rtl)})
    monkeypatch.setattr(pg, "lint_top_level", Mock(return_value={"clean": True}))
    # Stub tool/agent boundaries only: parsing, adoption, and hierarchy decisions are real.
    monkeypatch.setattr("orchestrator.harness.hierarchy.elaborate_hierarchy",
                        lambda sources, top, **k: set() if top == "leaf" else {"leaf"})
    monkeypatch.setattr("orchestrator.langchain.agents.integration_lead.IntegrationLeadAgent",
                        Mock(side_effect=AssertionError("single-block path called an agent")))
    monkeypatch.setattr(pg, "_resolve_interrupt", AsyncMock(return_value={"action": "abort"}))
    return tmp_path, rtl


def integrate(root):
    return asyncio.run(pg.integration_check_node({
        "project_root": str(root), "block_queue": [{"name": "leaf"}],
    }))["integration_result"]


def assert_adopted(root, result, top, rtl, wrapper):
    assert result.get("lint_clean") is True, result
    assert not result.get("aborted")
    rec = tm.validated_candidate(root)
    record = json.loads((root / ".coresmith/integration_result.json").read_text())
    for data in (result, rec, record):
        assert data["top_module"] == top
        assert data["top_rtl_path"] == str(rtl)
    assert record["candidate_sha"] == rec["candidate_sha"]
    assert result["single_block_wrapper"] is wrapper
    return rec


@pytest.mark.parametrize("top", ["leaf", "envelope"])
def test_declared_module_in_block_file_is_adopted_without_wrapper(project, top):
    root, rtl = project
    (root / "inputs/task.yaml").write_text(f"chassis: none\ntop: {top}\n")
    if top == "envelope":
        # The declared top need not be the block name or first module in its file.
        with rtl.open("a") as stream:
            stream.write("module envelope(input a, output y); leaf u(a,y); endmodule\n")
    rec = assert_adopted(root, integrate(root), top, rtl, False)
    assert rec["sources"] == [str(rtl)]
    assert not list((root / "rtl/integration").glob("*.v"))
    pg._resolve_interrupt.assert_not_awaited()


def test_no_declared_top_keeps_passthrough_wrapper(project):
    root, rtl = project
    (root / "inputs/task.yaml").write_text("chassis: none\n")
    wrapper = root / "rtl/integration/friendly_design.v"
    rec = assert_adopted(root, integrate(root), "friendly_design_top", wrapper, True)
    assert "leaf u_leaf" in wrapper.read_text()
    assert set(rec["sources"]) == {str(rtl), str(wrapper)}
    pg._resolve_interrupt.assert_not_awaited()


@pytest.mark.parametrize("declaration", ["", "// module absent(); endmodule\n",
                                       "`ifdef NEVER\nmodule absent(); endmodule\n`endif\n"])
def test_missing_declared_top_parks_without_chassis_wording(project, declaration, capsys):
    root, rtl = project
    # A rejected replacement must not leave an earlier success behind.
    tm.write_candidate_receipt(root, "leaf", str(rtl), {"leaf": str(rtl)}, expected_blocks=[],
                               integration_result={"lint_clean": True})
    (root / "inputs/task.yaml").write_text("chassis: none\ntop: absent\n")
    with rtl.open("a") as stream:
        stream.write(declaration)
    result = integrate(root)
    payload = pg._resolve_interrupt.await_args.args[0]
    assert "declared top mismatch on the single-block path" in result["reason"]
    assert payload["phase"] == "single_block"
    assert payload["deterministic"] is True
    assert "absent" in json.dumps(payload)
    assert "caravel" not in (json.dumps(payload) + json.dumps(result) + capsys.readouterr().out).lower()
    assert result.get("aborted")
    assert not (root / ".coresmith/integration_result.json").exists()
    assert not (root / tm.RECEIPT_REL).exists()
    assert not list((root / "rtl/integration").glob("*.v"))
    pg.lint_top_level.assert_not_called()


def test_declared_top_still_requires_elaborated_block(project, monkeypatch):
    root, rtl = project
    (root / "inputs/task.yaml").write_text("chassis: none\ntop: envelope\n")
    with rtl.open("a") as stream:
        stream.write("module envelope(); endmodule\n")
    monkeypatch.setattr("orchestrator.harness.hierarchy.elaborate_hierarchy", lambda *a, **k: set())
    result = integrate(root)
    assert result.get("aborted")
    assert "leaf" in json.dumps(pg._resolve_interrupt.await_args.args[0])
    assert not (root / tm.RECEIPT_REL).exists()
    assert not (root / ".coresmith/integration_result.json").exists()


def test_lint_rejection_removes_prior_success(project):
    root, rtl = project
    assert_adopted(root, integrate(root), "leaf", rtl, False)
    pg.lint_top_level.return_value = {"clean": False, "errors": "invalid RTL"}
    result = integrate(root)
    assert result.get("aborted") and not result["lint_clean"]
    assert "invalid RTL" in json.dumps(result)
    assert not (root / tm.RECEIPT_REL).exists()
    assert not (root / ".coresmith/integration_result.json").exists()


@pytest.mark.parametrize("action", ["retry", "fix_rtl"])
def test_single_block_park_can_resume_after_input_fix(project, action):
    root, rtl = project
    (root / "inputs/task.yaml").write_text("chassis: none\ntop: absent\n")
    pg._resolve_interrupt.return_value = {"action": action}
    result = integrate(root)
    assert result["retry_requested"] and result["action_taken"] == action
    assert not (root / ".coresmith/integration_result.json").exists()
    (root / "inputs/task.yaml").write_text("chassis: none\ntop: leaf\n")
    assert_adopted(root, integrate(root), "leaf", rtl, False)


def test_declared_top_is_adopted_with_real_tools(project, monkeypatch):
    if not shutil.which("yosys") or not shutil.which("verilator"):
        pytest.skip("requires yosys and verilator")
    monkeypatch.setattr("orchestrator.harness.hierarchy.elaborate_hierarchy", elaborate_hierarchy)
    monkeypatch.setattr(pg, "lint_top_level", ih.lint_top_level)
    root, rtl = project
    assert_adopted(root, integrate(root), "leaf", rtl, False)


def test_manifest_top_reaches_backend_probe_and_report(project, monkeypatch):
    root, rtl = project
    result = integrate(root)
    assert_adopted(root, result, "leaf", rtl, False)
    backend = asyncio.run(bg.init_design_node({"project_root": str(root), "design_name": "wrong"}))
    assert backend["design_name"] == "leaf"
    assert backend["integration_top_path"] == str(rtl)
    assert pg._resolve_probe_top("wrong", "module wrong(); endmodule", str(root)) == "leaf"
    # Exercise the flat-synthesis node up to its agent boundary.
    synth = AsyncMock(return_value={"success": False, "error": "stubbed synthesis"})
    monkeypatch.setattr(bg, "_run_llm_eda_step", synth)
    monkeypatch.setenv("CORESMITH_SRAM_MACRO", "0")
    asyncio.run(bg.flat_top_synthesis_node({
        **backend, "project_root": str(root), "design_name": "wrong",
    }))
    context = synth.await_args.kwargs["context"]
    assert context["design_name"] == "leaf"
    assert str(rtl) in context["input_files"]
    assert "friendly_design" not in context["input_files"]
    scoreboard = Mock()
    scoreboard.latest_dv.return_value = []
    scoreboard.latest_ppa.side_effect = lambda name: {"wns_ns": 0} if name == "leaf" else None
    report = fr.build_final_report({"integration_result": result, "design_name": "wrong"},
                                   str(root), scoreboard=scoreboard)
    assert report["top_module"] == "leaf"
    assert report["top_rtl_path"] == str(rtl)
    assert report["signoff"]["top_fmax_mhz"] == 50
    assert "Top module: `leaf`" in fr.render_markdown(report)


@pytest.mark.parametrize("stale", [False, True])
def test_report_does_not_guess_top_without_current_manifest(project, stale):
    root, rtl = project
    if stale:
        assert_adopted(root, integrate(root), "leaf", rtl, False)
        rtl.write_text("module leaf(); endmodule\n")
    report = fr.build_final_report({"design_name": "wrong"}, str(root))
    assert report.get("top_module") is None
    assert report["signoff"]["top_fmax_mhz"] is None
    assert "Top module: unknown" in fr.render_markdown(report)


@pytest.mark.parametrize("scope", ["integration", "validation"])
def test_dv_selects_adopted_top_even_when_design_name_disagrees(project, monkeypatch, scope):
    root, rtl = project
    assert_adopted(root, integrate(root), "leaf", rtl, False)
    tb = root / "test_design.py"
    tb.write_text("import cocotb\n")

    class StopAtSimulator(Exception):
        pass

    def simulator(cmd, **kwargs):
        makefile = (Path(cmd[2]) / "Makefile").read_text()
        assert "TOPLEVEL = leaf\n" in makefile
        assert f"VERILOG_SOURCES = {rtl}\n" in makefile
        raise StopAtSimulator

    monkeypatch.setattr(ih.subprocess, "Popen", simulator)
    with pytest.raises(StopAtSimulator):
        ih.run_integration_simulation("wrong", str(rtl), {"leaf": str(rtl)}, str(tb),
                                      project_root=root, sim_scope=scope)


@pytest.mark.parametrize("chassis", ["none", "caravel"])
def test_chassis_park_wording_requires_selected_profile(project, chassis):
    root, rtl = project
    (root / "inputs/task.yaml").write_text(f"chassis: {chassis}\n")
    asyncio.run(pg._park_caravel_assembly_failure(str(root), "Friendly Design", {"leaf": str(rtl)},
                                                "top module mismatch", ["mismatch"], str(rtl)))
    payload = pg._resolve_interrupt.await_args.args[0]
    assert ("caravel" in json.dumps(payload).lower()) is (chassis == "caravel")
