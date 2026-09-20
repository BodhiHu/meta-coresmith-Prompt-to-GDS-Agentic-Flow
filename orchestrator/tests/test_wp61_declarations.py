"""Chassis and AXIS stimulus policy require task-owned declarations."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from orchestrator.chassis import profile
from orchestrator.langgraph import acceptance_dv as ad
from orchestrator.langgraph import integration_helpers as ih
from orchestrator.langgraph import pipeline_graph as pg
from orchestrator.langgraph.bfm_lib.classifier import classify_bus_verdict


@pytest.mark.parametrize("declaration", ["", "chassis: none\n", "chassis: off\n", "chassis: 0\n"])
def test_absent_or_disabled_chassis_has_no_pad_policy(tmp_path, monkeypatch, declaration):
    monkeypatch.delenv("CORESMITH_CHASSIS", raising=False)
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/task.yaml").write_text("top: user_project_wrapper\n" + declaration)
    assert profile.locked_boundary_ports(tmp_path) == ()
    verdict = classify_bus_verdict(str(tmp_path), "module chip(input csn); endmodule",
                                   [{"protocol": "qspi", "name": "qspi"}], "chip", "")
    assert verdict.status == "no_chassis" and not verdict.contract_enforcing


def test_task_chassis_precedes_environment(tmp_path, monkeypatch):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/task.yaml").write_text("chassis: none\n")
    monkeypatch.setenv("CORESMITH_CHASSIS", "caravel")
    assert profile.resolve_profile(tmp_path) is None
    (tmp_path / "inputs/task.yaml").write_text("chassis: caravel\n")
    monkeypatch.setenv("CORESMITH_CHASSIS", "none")
    assert profile.resolve_profile(tmp_path) == profile.CARAVEL


def test_declared_top_never_selects_caravel(tmp_path, monkeypatch):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/task.yaml").write_text("top: chip_top\nchassis: none\n")
    paths = {}
    for name in ["chip_top", "leaf"]:
        path = tmp_path / f"{name}.v"
        path.write_text(f"module {name}(input clk, output y); assign y=clk; endmodule")
        paths[name] = str(path)
    monkeypatch.setenv("CORESMITH_DETERMINISTIC_CARAVEL_TOP", "1")
    monkeypatch.setattr(pg, "_current_phase_completed", lambda *a: [{"name": n, "success": True} for n in paths])
    monkeypatch.setattr(pg, "load_architecture_connections", lambda *a: ([], "chip_top"))
    monkeypatch.setattr(pg, "discover_block_rtl", lambda *a: paths)
    monkeypatch.setattr(ih, "generate_caravel_wrapper_top", lambda *a: pytest.fail("undeclared chassis selected"))
    monkeypatch.setattr(pg, "_resolve_interrupt", AsyncMock(return_value={"action": "abort"}))
    monkeypatch.setattr("orchestrator.langchain.agents.integration_lead.IntegrationLeadAgent",
                        lambda *a, **k: SimpleNamespace(integrate=AsyncMock(side_effect=RuntimeError("stub reviewer unavailable"))))
    asyncio.run(pg.integration_check_node({"project_root": str(tmp_path), "block_queue": [{"name": n} for n in paths]}))


def test_undeclared_axis_packing_and_geometry_are_refused():
    assert ad.map_stimulus([0x12345678, 0xabcdef12], {"sidebands": {}, "s_axis": {"data_width": 32}}) is None
    assert ad.map_stimulus({"frames": [[1, 2, 3], [4, 5, 6]]},
                           {"sidebands": {"word_width": 32, "queue_height": 32}}) is None


def test_declared_axis_words_preserve_every_bit():
    contract = {"s_axis": {"data_width": 32}, "m_axis": {"data_width": 32}, "sidebands": {}}
    mapping = {"payload": "words", "input_width": 32, "output_width": 32,
               "packing": "words", "byte_order": "little", "sidebands": {}}
    result = ad.map_stimulus({"words": [0x12345678, 0xabcdef12]}, contract, mapping=mapping)
    assert result["payload"] == list(bytes.fromhex("7856341212efcdab"))


def test_missing_axis_mapping_parks_the_real_gate(tmp_path, monkeypatch):
    from orchestrator.tests.test_wp38_acceptance_receipts import _env, _project
    root, top = _project(tmp_path, "cases=[('one', {'data': [1], 'mode': 1})]\n")
    (root / "inputs/acceptance_stimulus.py").write_text("cases=[('one', {'data': [1], 'mode': 1})]\n")
    _env(monkeypatch, root)
    monkeypatch.setattr(ad, "_build", lambda *a, **k: pytest.fail("undeclared mapping reached compiler"))
    out = ad.run_acceptance_dv(str(root), str(top), {})
    assert not out["passed"] and out["oracle_incomplete"] and out["kind"] == "oracle_incomplete"
