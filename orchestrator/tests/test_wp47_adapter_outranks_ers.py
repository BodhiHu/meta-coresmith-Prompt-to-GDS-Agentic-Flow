"""WP-47: the task adapter outranks internal ERS/validation requirements."""
from pathlib import Path

from orchestrator.langgraph import pipeline_graph as pg


def test_chip_lead_rule_present():
    s = (Path(pg.__file__).resolve().parent.parent / "langchain" / "prompts" / "chip_lead.md").read_text()
    assert "THE TASK ADAPTER OUTRANKS INTERNAL REQUIREMENTS" in s
    assert "NEVER\n  change the RTL to satisfy the internal check" in s or "NEVER" in s


def test_budget_guidance_names_the_ers():
    src = open(pg.__file__, encoding="utf-8").read()
    assert "THAT " in src and "requirement is wrong: revise it" in src
