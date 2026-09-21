# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""LLM EDA steps must consume only a fresh result from a successful call."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.langchain.agents.coresmith_llm import ClaudeLLM
from orchestrator.langgraph import backend_graph as bg


async def _run(result_path: Path):
    return await bg._run_llm_eda_step(
        "test synth",
        "backend_synth_llm.md",
        {
            "design_name": "top",
            "target_clock_mhz": 50,
            "period_ns": 20,
            "liberty_path": "cells.lib",
            "output_dir": str(result_path.parent),
            "input_files": "top.v",
            "input_delay_ns": 4,
            "output_delay_ns": 4,
            "attempt": 1,
            "prior_failure": "none",
            "constraints": "none",
            "result_json_path": str(result_path),
            "sram_macro_directive": "none",
            "sram_wrapper_lib": "none",
            "constant_mapping_command": "hilomap",
        },
        str(result_path),
    )


@pytest.mark.asyncio
async def test_failed_call_rejects_and_archives_prior_pass(tmp_path, monkeypatch):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({"success": True, "stale": True}))

    async def failed_call(*_args, **_kwargs):
        return "[ClaudeLLM error: OpenCode did not finish normally: incomplete]"

    monkeypatch.setattr(ClaudeLLM, "call", failed_call)

    result = await _run(result_path)

    assert result["success"] is False
    assert result["_driver_failure"] is True
    assert "unsuccessful" in result["error"]
    assert not result_path.exists()
    archive = Path(result["prior_result_archive"])
    assert json.loads(archive.read_text()) == {"success": True, "stale": True}


@pytest.mark.asyncio
async def test_successful_call_adopts_fresh_dict_and_keeps_prior(tmp_path, monkeypatch):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({"success": True, "generation": 1}))

    async def successful_call(*_args, **_kwargs):
        result_path.write_text(json.dumps({"success": True, "generation": 2}))
        return "completed normally"

    monkeypatch.setattr(ClaudeLLM, "call", successful_call)

    result = await _run(result_path)

    assert result["success"] is True
    assert result["generation"] == 2
    assert json.loads(Path(result["prior_result_archive"]).read_text())[
        "generation"
    ] == 1


@pytest.mark.asyncio
async def test_normal_reply_without_fresh_result_fails(tmp_path, monkeypatch):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({"success": True}))

    async def no_write(*_args, **_kwargs):
        return "completed normally"

    monkeypatch.setattr(ClaudeLLM, "call", no_write)

    result = await _run(result_path)

    assert result["success"] is False
    assert result["_driver_failure"] is True
    assert "fresh result JSON" in result["error"]


@pytest.mark.asyncio
async def test_error_reply_quarantines_new_pass_looking_result(
    tmp_path, monkeypatch,
):
    result_path = tmp_path / "result.json"

    async def write_then_fail(*_args, **_kwargs):
        result_path.write_text(json.dumps({"success": True}))
        return "[ClaudeLLM error: OpenCode provider error: disconnected]"

    monkeypatch.setattr(ClaudeLLM, "call", write_then_fail)

    result = await _run(result_path)

    assert result["success"] is False
    assert result["_driver_failure"] is True
    assert not result_path.exists()
    assert json.loads(Path(result["rejected_result_archive"]).read_text()) == {
        "success": True,
    }


@pytest.mark.asyncio
async def test_non_object_fresh_result_fails(tmp_path, monkeypatch):
    result_path = tmp_path / "result.json"

    async def list_write(*_args, **_kwargs):
        result_path.write_text("[]")
        return "completed normally"

    monkeypatch.setattr(ClaudeLLM, "call", list_write)

    result = await _run(result_path)

    assert result["success"] is False
    assert result["_driver_failure"] is True
    assert "must be an object" in result["error"]


@pytest.mark.asyncio
async def test_drc_driver_failure_cannot_salvage_old_report_or_artifacts(
    tmp_path, monkeypatch,
):
    pnr = tmp_path / "syn" / "output" / "top" / "pnr"
    pnr.mkdir(parents=True)
    routed = pnr / "top_routed.def"
    routed.write_text("VERSION 5.8 ;\n")
    (pnr / "magic_drc.rpt").write_text("Total DRC errors found: 0\n")
    (pnr / "top.gds").write_bytes(b"stale-gds")
    (pnr / "top.spice").write_text("* stale spice\n")

    async def failed_driver(**_kwargs):
        return {
            "success": False,
            "_driver_failure": True,
            "error": "LLM call failed before fresh result",
        }

    monkeypatch.setattr(bg, "_run_llm_eda_step", failed_driver)
    result = await bg.drc_node({
        "project_root": str(tmp_path),
        "current_block": {"name": "top"},
        "attempt": 1,
        "route_result": {"success": True},
        "routed_def_path": str(routed),
    })

    assert result["drc_result"]["clean"] is False
    assert "gds_path" not in result
    assert "spice_path" not in result
    assert "LLM call failed" in result["previous_error"]


@pytest.mark.asyncio
async def test_lvs_driver_failure_cannot_be_reclassified_from_old_report(
    tmp_path, monkeypatch,
):
    pnr = tmp_path / "syn" / "output" / "top" / "pnr"
    pnr.mkdir(parents=True)
    spice = pnr / "top.spice"
    verilog = pnr / "top_pwr.v"
    spice.write_text(".subckt top\n.ends\n")
    verilog.write_text("module top; endmodule\n")
    (pnr / "top_lvs.rpt").write_text("Netlists match uniquely.\n")

    async def failed_driver(**_kwargs):
        return {
            "success": False,
            "_driver_failure": True,
            "error": "LLM call failed before fresh result",
        }

    monkeypatch.setattr(bg, "_run_llm_eda_step", failed_driver)
    result = await bg.lvs_node({
        "project_root": str(tmp_path),
        "current_block": {"name": "top"},
        "attempt": 1,
        "drc_result": {"clean": True},
        "spice_path": str(spice),
        "pwr_verilog_path": str(verilog),
    })

    assert result["lvs_result"]["match"] is False
    assert "LLM call failed" in result["previous_error"]
