"""Exercise real checkpoint replay, with an author that changes on every call."""
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from orchestrator.langgraph import pipeline_graph as pg


@pytest.mark.asyncio
@pytest.mark.parametrize("edit_reviewed_file", [False, True])
async def test_approval_does_not_reauthor(tmp_path, monkeypatch, edit_reviewed_file):
    from orchestrator.langchain.agents.integration_lead import IntegrationLeadAgent

    monkeypatch.setenv("CORESMITH_DETERMINISTIC_INTEGRATION_CHECK", "0")
    monkeypatch.delenv("CORESMITH_NONBLOCKING_INTEGRATION_WARNINGS", raising=False)
    monkeypatch.setattr(pg, "write_graph_event", lambda *a, **kw: None)
    monkeypatch.setattr(pg, "load_architecture_connections", lambda *a: ([], "chip_top"))
    paths = {}
    for name in ("a", "b"):
        path = tmp_path / f"{name}.v"
        path.write_text(f"module {name}(input clk); endmodule\n")
        paths[name] = str(path)
    monkeypatch.setattr(pg, "discover_block_rtl", lambda *a: paths)
    monkeypatch.setattr(pg, "lint_top_level", lambda *a, **k: {"clean": True})
    monkeypatch.setattr(
        "orchestrator.langchain.agents.integration_lead.assert_blocks_instantiated",
        lambda *a, **k: None)
    receipt = []
    monkeypatch.setattr("orchestrator.harness.top_module.write_candidate_receipt",
                        lambda *a, **k: receipt.append(k))
    calls = []

    async def author(self, **kwargs):
        calls.append(kwargs)
        path = tmp_path / "chip_top.v"
        path.write_text(f"module chip_top(input clk); // revision {len(calls)}\n"
                        "a u_a(.clk(clk)); b u_b(.clk(clk)); endmodule\n")
        return {"module_name": "chip_top", "rtl_path": str(path),
                "mismatches": [{"severity": "warning", "description": "review me"}]}

    monkeypatch.setattr(IntegrationLeadAgent, "integrate", author)
    graph = StateGraph(pg.OrchestratorState)
    graph.add_node("integration_check", pg.integration_check_node)
    graph.add_edge(START, "integration_check")
    graph.add_edge("integration_check", END)
    app = graph.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "approval"}}
    state = {"project_root": str(tmp_path), "completed_blocks": [
        {"name": n, "success": True} for n in paths]}
    parked = await app.ainvoke(state, config)
    assert parked["__interrupt__"][0].value["type"] == "integration_warning_review"
    assert len(calls) == 1
    reviewed = (tmp_path / "chip_top.v").read_bytes()
    if edit_reviewed_file:
        (tmp_path / "chip_top.v").write_text("module chip_top(); endmodule\n")
    resumed = await app.ainvoke(Command(resume={"action": "accept"}), config)
    result = resumed["integration_result"]
    assert len(calls) == 1, "approval must not rerun the assembly author"
    if edit_reviewed_file:
        assert result["aborted"] is True
        assert result["error"] == "reviewed_artifacts_changed"
        assert not receipt
    else:
        assert result["accepted_warnings"] is True
        assert (tmp_path / "chip_top.v").read_bytes() == reviewed
        assert len(receipt) == 1
