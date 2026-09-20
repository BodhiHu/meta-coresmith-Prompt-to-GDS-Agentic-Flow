"""Regression tests for timing sign-off result adoption."""

from __future__ import annotations

import pytest


def _state(tmp_path):
    return {
        "project_root": str(tmp_path),
        "current_block": {"name": "chip_top"},
        "target_clock_mhz": 25.0,
        "synth_gate_count": 2346,
        "timing_result": {"met": True, "wns_ns": 5.0, "tns_ns": 0.0},
        "power_result": {"total_power_mw": 0.735},
        "floorplan_result": {"design_area_um2": 12345.0},
        "place_result": {"success": True},
        "previous_error": "stale gate-sim failure from attempt 1",
        "constraints": [],
    }


@pytest.mark.asyncio
async def test_explicit_fail_dominates_contradictory_timing_met(
        tmp_path, monkeypatch):
    from orchestrator.langchain.agents import backend_eda_agent
    from orchestrator.langgraph.backend_graph import timing_signoff_node

    class FakeAgent:
        def __init__(self, **_kwargs):
            pass

        async def analyze(self, context):
            return {
                "timing_met": True,
                "sign_off": "FAIL",
                "assessment": "Post-route extraction is missing.",
            }

    monkeypatch.setattr(backend_eda_agent, "BackendEDAAgent", FakeAgent)
    out = await timing_signoff_node(_state(tmp_path))

    assert out["timing_result"]["met"] is False
    assert out["timing_result"]["sign_off"] == "FAIL"
    assert "Post-route extraction is missing" in out["previous_error"]


@pytest.mark.asyncio
async def test_pass_clears_stale_error_and_does_not_invent_metrics(
        tmp_path, monkeypatch):
    from orchestrator.langchain.agents import backend_eda_agent
    from orchestrator.langgraph.backend_graph import timing_signoff_node

    captured = {}

    class FakeAgent:
        def __init__(self, **_kwargs):
            pass

        async def analyze(self, context):
            captured.update(context)
            return {"timing_met": True, "sign_off": "PASS"}

    monkeypatch.setattr(backend_eda_agent, "BackendEDAAgent", FakeAgent)
    out = await timing_signoff_node(_state(tmp_path))

    assert out["timing_result"]["met"] is True
    assert out["previous_error"] == ""
    assert captured["setup_slack_ns"] is None
    assert captured["hold_slack_ns"] is None
    assert captured["design_area_um2"] == 12345.0
    assert out["timing_result"]["setup_slack_ns"] is None
    assert out["timing_result"]["hold_slack_ns"] is None
