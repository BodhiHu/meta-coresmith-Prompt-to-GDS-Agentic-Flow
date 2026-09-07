"""WP-20: architecture review feedback is bounded."""
from __future__ import annotations

from orchestrator.langgraph import architecture_graph as ag


def _hist(n, phase="final_review"):
    return [{"phase": phase, "action": "feedback"} for _ in range(n)] + [
        {"phase": "block_diagram", "action": "continue"}]


def test_feedback_rounds_used_counts_only_the_phase():
    assert ag._feedback_rounds_used(_hist(2), "final_review") == 2
    assert ag._feedback_rounds_used(_hist(2), "block_diagram") == 0
    assert ag._feedback_rounds_used(None, "final_review") == 0


def test_cap_downgrades_feedback_to_accept(monkeypatch):
    monkeypatch.setenv("CORESMITH_ARCH_MAX_FINAL_FEEDBACK", "2")
    events = []
    monkeypatch.setattr(ag, "_event", lambda st, node, ev, data: events.append((node, ev, data)))
    st = {"round": 1, "human_response_history": _hist(2)}
    r, capped = ag._cap_feedback(st, "final_review", {"action": "feedback", "feedback": "nit"}, "Final Review")
    assert capped and r["action"] == "accept" and r["capped_feedback"] == "nit"
    assert events and events[0][1] == "feedback_cap"


def test_below_cap_feedback_passes_through(monkeypatch):
    monkeypatch.setenv("CORESMITH_ARCH_MAX_FINAL_FEEDBACK", "2")
    st = {"round": 1, "human_response_history": _hist(1)}
    r, capped = ag._cap_feedback(st, "final_review", {"action": "feedback", "feedback": "x"}, "Final Review")
    assert not capped and r["action"] == "feedback"


def test_block_diagram_cap_uses_continue(monkeypatch):
    monkeypatch.setenv("CORESMITH_ARCH_MAX_FINAL_FEEDBACK", "1")
    monkeypatch.setattr(ag, "_event", lambda *a, **k: None)
    st = {"round": 1, "human_response_history": _hist(1, "block_diagram")}
    r, capped = ag._cap_feedback(st, "block_diagram", {"action": "feedback", "feedback": "x"}, "Escalate Diagram")
    assert capped and r["action"] == "continue"


def test_prompts_follow_contract_reset():
    from pathlib import Path
    root = Path(ag.__file__).resolve().parent.parent
    bd = (root / "langchain/prompts/block_diagram.md").read_text()
    ir = (root / "langchain/prompts/integration_review.md").read_text()
    assert "wb_rst_i" in bd and "wb_rst_i" in ir
    from orchestrator.langchain.agents.chip_lead_agent import CHIP_LEAD_PROMPT
    assert "ARCHITECTURE REVIEW DISCIPLINE" in CHIP_LEAD_PROMPT
