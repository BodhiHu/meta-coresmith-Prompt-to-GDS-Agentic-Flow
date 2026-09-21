"""Regression tests for deterministic extracted timing sign-off adoption."""

from __future__ import annotations

from pathlib import Path

import pytest


def _state(tmp_path):
    synth = tmp_path / "syn" / "output" / "chip_top"
    pnr = synth / "pnr"
    pnr.mkdir(parents=True)
    netlist = synth / "chip_top_netlist.v"
    sdc = synth / "chip_top.sdc"
    routed = pnr / "chip_top_routed.def"
    netlist.write_text("module chip_top(input clk); endmodule\n")
    sdc.write_text("create_clock -period 40 [get_ports clk]\n")
    routed.write_text("DESIGN chip_top ;\nEND DESIGN\n")
    return {
        "project_root": str(tmp_path),
        "current_block": {"name": "chip_top"},
        "target_clock_mhz": 25.0,
        "synth_gate_count": 2346,
        "flat_netlist_path": str(netlist),
        "flat_sdc_path": str(sdc),
        "routed_def_path": str(routed),
        "attempt": 1,
        "timing_result": {"met": True, "wns_ns": 5.0, "tns_ns": 0.0},
        "power_result": {"total_power_mw": 0.735},
        "floorplan_result": {"design_area_um2": 12345.0},
        "place_result": {"success": True},
        "previous_error": "stale gate-sim failure from attempt 1",
        "constraints": [],
    }


@pytest.mark.asyncio
async def test_extracted_failure_dominates_positive_pnr_estimate(
        tmp_path, monkeypatch):
    from orchestrator.langgraph import extracted_timing
    from orchestrator.langgraph.backend_graph import timing_signoff_node

    def fake_extracted(**_kwargs):
        return {
            "met": False,
            "sign_off": "FAIL",
            "source": "extracted_rcx_sta",
            "extraction_complete": True,
            "setup_slack_ns": -0.25,
            "hold_slack_ns": 0.10,
            "wns_ns": -0.25,
            "tns_ns": -1.0,
            "failure_reasons": ["setup slack is negative (-0.25 ns)"],
        }

    monkeypatch.setattr(extracted_timing, "run_extracted_timing", fake_extracted)
    out = await timing_signoff_node(_state(tmp_path))

    assert out["timing_result"]["met"] is False
    assert out["timing_result"]["sign_off"] == "FAIL"
    assert out["timing_result"]["source"] == "extracted_rcx_sta"
    assert "setup slack is negative" in out["previous_error"]


@pytest.mark.asyncio
async def test_measured_pass_clears_stale_error_and_adopts_artifacts(
        tmp_path, monkeypatch):
    from orchestrator.langgraph import extracted_timing
    from orchestrator.langgraph.backend_graph import timing_signoff_node

    captured = {}

    def fake_extracted(**kwargs):
        captured.update(kwargs)
        return {
            "met": True,
            "sign_off": "PASS",
            "source": "extracted_rcx_sta",
            "extraction_complete": True,
            "setup_slack_ns": 22.21,
            "hold_slack_ns": 0.46,
            "wns_ns": 0.0,
            "tns_ns": 0.0,
            "failure_reasons": [],
            "spef_path": str(tmp_path / "chip_top.spef"),
            "total_power_mw": 0.758,
            "design_area_um2": 21010.0,
            "utilization_pct": 51.0,
        }

    monkeypatch.setattr(extracted_timing, "run_extracted_timing", fake_extracted)
    out = await timing_signoff_node(_state(tmp_path))

    assert out["timing_result"]["met"] is True
    assert out["previous_error"] == ""
    assert captured["routed_def_path"].endswith("chip_top_routed.def")
    assert captured["sdc_path"].endswith("chip_top.sdc")
    assert out["spef_path"].endswith("chip_top.spef")
    assert out["power_result"]["total_power_mw"] == pytest.approx(0.758)
    assert out["place_result"]["design_area_um2"] == pytest.approx(21010.0)


@pytest.mark.asyncio
async def test_missing_routed_artifact_fails_without_model_call(tmp_path):
    from orchestrator.langgraph.backend_graph import timing_signoff_node

    state = _state(tmp_path)
    state["routed_def_path"] = str(tmp_path / "missing.def")
    out = await timing_signoff_node(state)

    assert out["timing_result"]["met"] is False
    assert out["timing_result"]["extraction_complete"] is False
    assert "routed DEF is missing" in out["previous_error"]


@pytest.mark.asyncio
async def test_completion_rejects_estimated_or_missing_timing_evidence(tmp_path):
    from orchestrator.langgraph.backend_graph import advance_block_node

    state = _state(tmp_path)
    state.update({
        "drc_result": {"clean": True},
        "lvs_result": {"match": True},
        "route_result": {"success": True},
        "step_log_paths": {},
    })
    # This is the exact old false pass: PnR estimate says met, but no RCX ran.
    state["timing_result"] = {"met": True, "wns_ns": 0.0}
    result = await advance_block_node(state)
    assert result["completed_blocks"][0]["success"] is False
    assert result["completed_blocks"][0]["timing_met"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gate_ok", "gate_status", "expected"),
    [
        (True, "pass", True),
        (None, "disabled", True),
        (None, "not_run", False),
        (False, "disabled", False),
    ],
)
async def test_completion_gate_sim_backstop(
        tmp_path, gate_ok, gate_status, expected):
    from orchestrator.langgraph.backend_graph import advance_block_node

    state = _state(tmp_path)
    state.update({
        "drc_result": {"clean": True},
        "lvs_result": {"match": True},
        "timing_result": {
            "met": True,
            "source": "extracted_rcx_sta",
            "extraction_complete": True,
            "sign_off": "PASS",
        },
        "route_result": {"success": True},
        "step_log_paths": {},
        "chip_gate_sim_ok": gate_ok,
        "chip_gate_sim_status": gate_status,
        "chip_gate_sim_reason": "test evidence",
    })
    result = await advance_block_node(state)
    block = result["completed_blocks"][0]
    assert block["success"] is expected
    assert block["chip_gate_sim_accepted"] is expected


@pytest.mark.asyncio
async def test_completion_adopts_extracted_area_and_routed_def_geometry(tmp_path):
    from orchestrator.langgraph.backend_graph import advance_block_node

    state = _state(tmp_path)
    state["routed_def_path"] = str(
        tmp_path / "syn" / "output" / "chip_top" / "pnr" / "chip_top_routed.def"
    )
    Path(state["routed_def_path"]).write_text(
        "UNITS DISTANCE MICRONS 1000 ;\n"
        "DIEAREA ( 0 0 ) ( 252415 252415 ) ;\n"
    )
    state.update({
        "drc_result": {"clean": True},
        "lvs_result": {"match": True},
        "timing_result": {
            "met": True, "source": "extracted_rcx_sta",
            "extraction_complete": True, "sign_off": "PASS",
            "design_area_um2": 24572.0, "utilization_pct": 40.0,
        },
        "route_result": {"success": True},
        "step_log_paths": {},
        "chip_gate_sim_ok": True,
        "chip_gate_sim_status": "pass",
    })
    block = (await advance_block_node(state))["completed_blocks"][0]
    assert block["design_area_um2"] == pytest.approx(24572.0)
    assert block["utilization_pct"] == pytest.approx(40.0)
    assert block["die_area_um2"] == pytest.approx(252.415**2)


@pytest.mark.asyncio
async def test_dashboard_keeps_extracted_metrics_over_pnr_estimates(
        tmp_path, monkeypatch):
    from orchestrator.langgraph import backend_helpers
    from orchestrator.langgraph.backend_graph import backend_complete_node

    pnr = tmp_path / "syn" / "output" / "chip_top" / "pnr"
    pnr.mkdir(parents=True)
    (tmp_path / ".coresmith").mkdir()
    routed_def = pnr / "chip_top_routed.def"
    routed_def.write_text(
        "UNITS DISTANCE MICRONS 1000 ;\n"
        "DIEAREA ( 0 0 ) ( 252415 252415 ) ;\n"
    )
    monkeypatch.setattr(backend_helpers, "parse_openroad_reports", lambda _p: {
        "wns_ns": -99.0,
        "setup_slack_ns": -99.0,
        "timing_met": False,
        "design_area_um2": 123.0,
    })
    state = {
        "project_root": str(tmp_path),
        "completed_blocks": [{
            "name": "chip_top",
            "success": True,
            "timing_met": True,
            "timing_source": "extracted_rcx_sta",
            "timing_sign_off": "PASS",
            "timing_extraction_complete": True,
            "timing_wns_ns": 0.0,
            "timing_tns_ns": 0.0,
            "setup_slack_ns": 22.21,
            "hold_slack_ns": 0.46,
            "total_power_mw": 0.758,
            "design_area_um2": 24572.0,
            "utilization_pct": 40.0,
            "routed_def_path": str(routed_def),
            "chip_gate_sim_ok": True,
            "chip_gate_sim_status": "pass",
            "chip_gate_sim_accepted": True,
        }],
    }

    await backend_complete_node(state)
    import json
    payload = json.loads(
        (tmp_path / ".coresmith" / "backend_results.json").read_text()
    )
    block = payload["blocks"][0]
    assert block["timing_met"] is True
    assert block["timing_source"] == "extracted_rcx_sta"
    assert block["setup_slack_ns"] == pytest.approx(22.21)
    assert block["hold_slack_ns"] == pytest.approx(0.46)
    assert block["design_area_um2"] == pytest.approx(24572.0)
    assert block["utilization_pct"] == pytest.approx(40.0)
    assert block["die_area_um2"] == pytest.approx(252.415**2)


@pytest.mark.asyncio
async def test_backend_results_report_unknown_area_as_null(tmp_path):
    from orchestrator.langgraph.backend_graph import backend_complete_node

    (tmp_path / ".coresmith").mkdir()
    state = {
        "project_root": str(tmp_path),
        "completed_blocks": [{
            "name": "chip_top", "success": True, "timing_met": True,
            "design_area_um2": None, "die_area_um2": None,
            "utilization_pct": None,
        }],
    }
    await backend_complete_node(state)
    import json
    block = json.loads(
        (tmp_path / ".coresmith" / "backend_results.json").read_text()
    )["blocks"][0]
    assert block["design_area_um2"] is None
    assert block["die_area_um2"] is None
    assert block["utilization_pct"] is None


def test_def_die_area_parser_uses_declared_dbu(tmp_path):
    from orchestrator.langgraph.backend_helpers import die_area_um2_from_def

    routed_def = tmp_path / "routed.def"
    routed_def.write_text(
        "UNITS DISTANCE MICRONS 2000 ;\n"
        "DIEAREA ( -1000 2000 ) ( 499000 502000 ) ;\n"
    )
    assert die_area_um2_from_def(routed_def) == pytest.approx(250.0 * 250.0)
    assert die_area_um2_from_def(tmp_path / "missing.def") is None
