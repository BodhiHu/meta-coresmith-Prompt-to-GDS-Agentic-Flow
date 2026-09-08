"""WP-22: nested-hierarchy postcondition and the postcondition park."""
from __future__ import annotations

import inspect

from orchestrator.langchain.agents.integration_lead import assert_blocks_instantiated
from orchestrator.langgraph import pipeline_graph as pg


def test_nested_hierarchy_counts_only_what_the_top_reaches():
    top = "module chip_top(); outer_shell u_o(); endmodule\n"
    wrapper = ("module pad_wrapper(); fft_engine u_f(); twiddle_rom u_t(); endmodule\n"
               "module outer_shell(); pad_wrapper u_w(); endmodule\n")
    assert assert_blocks_instantiated(top, {"fft_engine", "twiddle_rom"})  # top alone: missing
    assert assert_blocks_instantiated(top, {"fft_engine", "twiddle_rom"}, sources=[wrapper]) is None
    # WP-49: an instance inside a module the top never reaches does not count
    orphan = "module unused_holder(); fft_engine u_f(); twiddle_rom u_t(); endmodule\n"
    assert assert_blocks_instantiated(top, {"fft_engine", "twiddle_rom"}, sources=[orphan])


def test_integration_node_reads_the_hierarchy_and_parks():
    src = inspect.getsource(pg.integration_check_node)
    assert "_hier_sources" in src and '"phase": "postcondition"' in src
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
