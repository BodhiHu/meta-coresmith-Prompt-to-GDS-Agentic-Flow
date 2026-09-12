"""WP-19: an acceptance failure parks for the chip lead instead of failing signoff."""
from __future__ import annotations

import inspect

from orchestrator.langgraph import pipeline_graph as pg


def test_acceptance_failure_returns_pending_decision():
    src = inspect.getsource(pg.validation_dv_node)
    i = src.find("[ACCEPTANCE-DV] FAILED")
    assert i > 0
    tail = src[i:i + 6000]
    assert '"pending_decision": True' in tail
    assert '"type": "validation_dv_failure"' in tail
    assert '"phase": "acceptance_dv"' in tail
    assert "ACCEPTANCE_DV_FAILURE" in tail


def test_route_parks_on_pending_decision():
    assert pg.route_after_validation_dv({"validation_dv_result": {
        "passed": False, "pending_decision": True}}) == "validation_dv_decision"


def test_chip_lead_prompt_covers_acceptance_phase():
    from orchestrator.langchain.agents.chip_lead_agent import CHIP_LEAD_PROMPT
    assert "phase: acceptance_dv" in CHIP_LEAD_PROMPT
