"""WP-24: a wrapper block that already is the graded top gets adopted as chip top."""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from orchestrator.langgraph import pipeline_graph as pg


def _mod(name, *ports):
    return SimpleNamespace(name=name, ports=[SimpleNamespace(name=p) for p in ports])


PADS = ("wb_clk_i", "wb_rst_i", "io_in", "io_out", "io_oeb")


def test_self_assembled_wrapper_detected():
    modules = {"user_project_wrapper": _mod("user_project_wrapper", *PADS),
               "fft_engine": _mod("fft_engine", "clk"), "twiddle_rom": _mod("twiddle_rom", "clk")}
    src = {"user_project_wrapper": "module user_project_wrapper(); fft_engine u_f(); twiddle_rom u_t(); endmodule"}
    assert pg._self_assembled_wrapper("user_project_wrapper", modules, src)


def test_pad_adapter_only_is_not_self_assembled():
    modules = {"user_project_wrapper": _mod("user_project_wrapper", *PADS),
               "fft_engine": _mod("fft_engine", "clk")}
    src = {"user_project_wrapper": "module user_project_wrapper(); endmodule"}
    assert not pg._self_assembled_wrapper("user_project_wrapper", modules, src)


def test_wrapper_without_pads_is_not_self_assembled():
    modules = {"user_project_wrapper": _mod("user_project_wrapper", "clk", "rst_n"),
               "fft_engine": _mod("fft_engine", "clk")}
    src = {"user_project_wrapper": "module user_project_wrapper(); fft_engine u_f(); endmodule"}
    assert not pg._self_assembled_wrapper("user_project_wrapper", modules, src)


def test_integration_node_adopts_self_assembled_wrapper():
    src = inspect.getsource(pg.integration_check_node)
    assert "_self_assembled_wrapper(" in src and '"self_assembled_wrapper": True' in src
