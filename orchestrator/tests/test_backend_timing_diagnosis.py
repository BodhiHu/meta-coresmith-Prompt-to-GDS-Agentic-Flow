from __future__ import annotations

import pytest


def _state(tmp_path, timing_result, **extra):
    state = {
        "project_root": str(tmp_path),
        "current_block": {"name": "chip_top"},
        "phase": "signoff",
        "attempt": 2,
        "max_attempts": 3,
        "attempt_history": [],
        "previous_error": "Extracted timing sign-off FAIL: coverage rejected",
        "timing_result": timing_result,
    }
    state.update(extra)
    return state


@pytest.mark.asyncio
async def test_signoff_diagnosis_preserves_non_slack_failure_and_rejects_continue(
    tmp_path, monkeypatch
):
    from orchestrator.architecture.specialists import tapeout_diagnosis
    from orchestrator.langgraph.backend_graph import diagnose_node

    captured = {}

    async def fake_diagnose(**kwargs):
        captured.update(kwargs)
        return {
            "category": "LVS_EXPECTED",
            "diagnosis": "Positive slack means all checks pass.",
            "confidence": 0.95,
            "action": "continue",
            "suggested_fix": "",
        }

    monkeypatch.setattr(tapeout_diagnosis, "diagnose_tapeout_failure", fake_diagnose)
    timing = {
        "met": False,
        "sign_off": "FAIL",
        "setup_slack_ns": 22.44,
        "hold_slack_ns": 0.46,
        "wns_ns": 0.0,
        "tns_ns": 0.0,
        "reports_complete": True,
        "extraction_complete": True,
        "functional_unannotated_drivers": ["u_core/tie0/HI"],
        "failure_reasons": ["functional drivers are wholly unannotated"],
        "error": "coverage rejected",
    }

    result = await diagnose_node(_state(tmp_path, timing))

    assert captured["timing_result"] is timing
    assert "functional drivers are wholly unannotated" in captured["error_summary"]
    assert "previous_error=Extracted timing sign-off FAIL" in captured["error_summary"]
    assert captured["pnr_params"] == {}
    assert result["debug_result"]["escalate"] is True
    assert result["debug_result"]["needs_human"] is True
    assert result["debug_result"]["category"] == "TIMING_DIAGNOSTIC_CONTRADICTION"
    assert "Diagnostic contradiction" in result["debug_result"]["diagnosis"]
    assert "functional drivers are wholly unannotated" in result["attempt_history"][0]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timing",
    [
        {"met": False, "wns_ns": -0.25, "tns_ns": -3.0,
         "failure_reasons": ["setup timing violated"]},
        {"met": False, "reports_complete": False, "extraction_complete": False,
         "failure_reasons": ["SPEF was not produced"]},
    ],
)
async def test_all_authoritative_timing_failures_guard_continue(
    tmp_path, monkeypatch, timing
):
    from orchestrator.architecture.specialists import tapeout_diagnosis
    from orchestrator.langgraph.backend_graph import diagnose_node

    async def fake_diagnose(**kwargs):
        return {"category": "PNR_FAILURE", "diagnosis": "continue",
                "confidence": 0.9, "action": "continue"}

    monkeypatch.setattr(tapeout_diagnosis, "diagnose_tapeout_failure", fake_diagnose)
    result = await diagnose_node(_state(tmp_path, timing))
    assert result["debug_result"]["next_action"] == "ask_human"


@pytest.mark.asyncio
async def test_nonfailed_or_nontiming_continue_is_unchanged_and_actual_pnr_values_passed(
    tmp_path, monkeypatch
):
    from orchestrator.architecture.specialists import tapeout_diagnosis
    from orchestrator.langgraph.backend_graph import diagnose_node

    captured = {}

    async def fake_diagnose(**kwargs):
        captured.update(kwargs)
        return {"category": "LVS_EXPECTED", "diagnosis": "benign",
                "confidence": 0.9, "action": "continue"}

    monkeypatch.setattr(tapeout_diagnosis, "diagnose_tapeout_failure", fake_diagnose)
    state = _state(
        tmp_path,
        {"met": True, "wns_ns": 0.1},
        phase="lvs",
        lvs_result={"match": False, "mismatches": ["power-only"]},
        place_result={"utilization": 35},
        route_result={"density": 0.55},
    )
    result = await diagnose_node(state)
    assert result["debug_result"]["escalate"] is False
    assert result["debug_result"]["next_action"] == "retry_pnr"
    assert captured["pnr_params"] == {"utilization": 35, "density": 0.55}


def test_timing_formatter_includes_gate_and_coverage_evidence():
    from orchestrator.architecture.specialists.tapeout_diagnosis import _format_timing

    text = _format_timing({
        "met": False,
        "failure_reasons": ["missing SPEF"],
        "functional_unannotated_drivers": ["u0/HI"],
    })
    assert '"met": false' in text
    assert "missing SPEF" in text
    assert "u0/HI" in text
