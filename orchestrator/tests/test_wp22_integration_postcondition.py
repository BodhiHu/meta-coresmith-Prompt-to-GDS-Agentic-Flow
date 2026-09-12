"""WP-22: nested-hierarchy postcondition and the postcondition park."""
from __future__ import annotations

import inspect

from orchestrator.langchain.agents.integration_lead import assert_blocks_instantiated
from orchestrator.langgraph import pipeline_graph as pg


def test_nested_hierarchy_counts_when_texts_are_joined():
    top = "module chip_top(); openframe_project_wrapper u_o(); endmodule\n"
    wrapper = ("module user_project_wrapper(); fft_engine u_f(); twiddle_rom u_t(); endmodule\n"
               "module openframe_project_wrapper(); user_project_wrapper u_w(); endmodule\n")
    assert assert_blocks_instantiated(top, {"fft_engine", "twiddle_rom"})  # top alone: missing
    assert assert_blocks_instantiated(top + wrapper, {"fft_engine", "twiddle_rom"}) is None


def test_integration_node_reads_the_hierarchy_and_parks():
    src = inspect.getsource(pg.integration_check_node)
    assert "_hier_text" in src and '"phase": "postcondition"' in src
    assert '"supported_actions": ["retry", "fix_rtl", "abort"]' in src


def test_route_retries_integration_check_after_postcondition_park():
    assert pg.route_after_integration({"integration_result": {"retry_requested": True}}) == "integration_check"
    assert pg.route_after_integration({"integration_result": {"aborted": True, "skipped": True}}) == pg.END


def test_prompts_follow_spec_reset():
    from pathlib import Path
    root = Path(pg.__file__).resolve().parent.parent
    for rel in ("langchain/prompts/rtl_generator.md", "langchain/prompts/testbench_generator.md",
                "langchain/prompts/uarch_spec_generator.md"):
        assert "wb_rst_i" in (root / rel).read_text(), rel
    assert "Use synchronous active-low reset (rst_n)." not in (root / "langchain/agents/rtl_generator.py").read_text()
