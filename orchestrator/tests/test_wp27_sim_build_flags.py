"""WP-27: sim builds are warning-tolerant like the published harness; the Caravel path persists its record."""
from __future__ import annotations

import inspect

from orchestrator.langgraph import integration_helpers as ih
from orchestrator.langgraph import pipeline_graph as pg
from orchestrator.langgraph import pipeline_helpers as ph


def test_integration_makefile_is_not_fatal_on_verilator_warnings():
    mk = ih._compose_dv_makefile("integration", "", "a.v b.v", "chip_top", "test_chip_top")
    assert "EXTRA_ARGS += -Wno-fatal" in mk


def test_block_makefile_is_not_fatal_on_verilator_warnings():
    src = inspect.getsource(ph)
    assert "EXTRA_ARGS += -Wno-fatal" in src


def test_caravel_assembled_result_is_persisted():
    src = inspect.getsource(pg.integration_check_node)
    i = src.find('"caravel_wrapper_assembled": True,')
    assert i > 0
    assert "integration_result=integration_result" in src[i:i + 3000]
