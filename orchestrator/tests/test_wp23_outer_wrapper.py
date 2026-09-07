"""WP-23: outer OpenFrame/MPW wrapper blocks are excluded at integration."""
from __future__ import annotations

from types import SimpleNamespace

from orchestrator.langgraph import pipeline_graph as pg


def _mod(*names):
    return SimpleNamespace(name="m", ports=[SimpleNamespace(name=n) for n in names])


def test_named_outer_wrapper_is_dropped():
    modules = {"openframe_project_wrapper": _mod("vdda1", "gpio_in", "gpio_out"),
               "user_project_wrapper": _mod("io_in", "io_out", "io_oeb", "wb_clk_i"),
               "fft_engine": _mod("clk", "in_valid", "in_data")}
    rtl = {k: f"/x/{k}.v" for k in modules}
    src = {k: "" for k in modules}
    outer = pg._drop_outer_wrapper_blocks(modules, rtl, src)
    assert outer == {"openframe_project_wrapper"}
    assert set(modules) == {"user_project_wrapper", "fft_engine"} == set(rtl) == set(src)


def test_chassis_only_block_is_outer_but_upw_is_kept():
    assert pg._is_outer_wrapper_block("shell", _mod("vdda1", "vssd1", "gpio_in", "wb_clk_i"))
    assert not pg._is_outer_wrapper_block("user_project_wrapper", _mod("io_in", "io_out", "io_oeb"))
    assert not pg._is_outer_wrapper_block("regmap", _mod("wb_clk_i", "cfg_valid", "cfg_data"))


def test_never_empties_the_design():
    modules = {"openframe_project_wrapper": _mod("vdda1")}
    assert pg._drop_outer_wrapper_blocks(modules, dict(modules), dict(modules)) == set()
    assert "openframe_project_wrapper" in modules


def test_prompts_forbid_outer_wrapper_blocks():
    from pathlib import Path
    root = Path(pg.__file__).resolve().parent.parent
    assert "NEVER add an `openframe_project_wrapper`" in (root / "langchain/prompts/block_diagram.md").read_text()
    from orchestrator.langchain.agents.chip_lead_agent import CHIP_LEAD_PROMPT
    assert "OUTER WRAPPER BLOCKS" in CHIP_LEAD_PROMPT
