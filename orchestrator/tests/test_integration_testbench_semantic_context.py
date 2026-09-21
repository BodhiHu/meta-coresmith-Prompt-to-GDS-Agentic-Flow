# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Integration DV receives canonical semantics instead of width-only edges."""

from __future__ import annotations

import json

import pytest

from orchestrator.langchain.agents.integration_testbench_generator import (
    SYSTEM_PROMPT,
    IntegrationTestbenchGenerator,
)
from orchestrator.langgraph.integration_helpers import (
    VerilogModule,
    VerilogPort,
    _load_integration_dv_semantics,
    generate_integration_testbench,
)


def _contract_doc():
    return {
        "default_packing_convention": "msb_first_by_field_list",
        "contracts": [{
            "edge_id": "rom_rsp",
            "producer_block": "spi_rom",
            "consumer_block": "mcu_core",
            "producer_port": "m_rsp_srdy/m_rsp_data",
            "consumer_port": "s_rsp_drdy/s_rsp_data",
            "data_width_bits": 16,
            "fields": [{"name": "rsp_instr", "width": 16, "msb": 15, "lsb": 0}],
            "handshake_protocol": "srdy_drdy",
            "semantic_contract": (
                "A redirected response is consumed but never retired"
            ),
            "rate_description": "One response for each accepted fetch request",
            "flow_control_policy": {
                "semantics": "request_response",
                "min_buffer_depth_beats": 2,
                "consumer_can_stall": True,
            },
            "bootstrap_policy": {
                "required": True,
                "policy_type": "request_driven",
            },
            "representations": {
                "state_semantics": [{
                    "name": "response_pairing",
                    "rule": "exactly one response per accepted request",
                }],
            },
            # Long implementation prose is deliberately not duplicated into
            # the DV prompt; the structured requirements above are sufficient.
            "notes": "implementation narrative that is not a DV requirement",
        }],
    }


def _invariants():
    return [{
        "id": "SYS-CHANNEL",
        "description": "Single outstanding request through response consumption",
        "required_state": "outstanding_count <= 1",
        "verification_method": "Handshake-hold assertions and flush regression",
        "affected_blocks": ["mcu_core", "spi_rom"],
    }]


@pytest.mark.asyncio
async def test_prompt_preserves_contract_semantics_and_system_invariant(tmp_path):
    output = tmp_path / "test_chip_top.py"
    captured = {}
    agent = IntegrationTestbenchGenerator()

    async def fake_call(**kwargs):
        captured.update(kwargs)
        return (
            "```python\n"
            "import cocotb\n\n"
            "@cocotb.test()\n"
            "async def test_contract(dut):\n"
            "    assert True\n"
            "```"
        )

    agent.llm.call = fake_call
    await agent.generate(
        design_name="chip_top",
        top_rtl_source="module chip_top(input clk); endmodule",
        block_summaries=[],
        connections=[{
            "from": "spi_rom",
            "to": "mcu_core",
            "interface": "fetch_rsp",
            "data_width": 16,
            "handshake_protocol": "srdy_drdy",
            "semantic_contract": (
                "rsp_instr[7:0] is the first ROM byte; wrong-path responses "
                "are drained without retirement"
            ),
        }],
        interface_contracts=_contract_doc(),
        system_invariants=_invariants(),
        output_path=str(output),
    )

    prompt = captured["prompt"]
    assert "rsp_instr[7:0] is the first ROM byte" in prompt
    assert '"name": "rsp_instr"' in prompt
    assert '"producer_payload_ports": ["m_rsp_rsp_instr"]' in prompt
    assert '"consumer_payload_ports": ["s_rsp_rsp_instr"]' in prompt
    assert "m_rsp_srdy/m_rsp_data" not in prompt
    assert '"min_buffer_depth_beats": 2' in prompt
    assert "exactly one response per accepted request" in prompt
    assert "A redirected response is consumed but never retired" in prompt
    assert "One response for each accepted fetch request" in prompt
    assert '"required_state": "outstanding_count <= 1"' in prompt
    assert "implementation narrative that is not a DV requirement" not in prompt
    assert "externally controllable backpressure when present" in prompt


@pytest.mark.asyncio
async def test_wrapper_loads_canonical_files_and_passes_context(tmp_path, monkeypatch):
    coresmith = tmp_path / ".coresmith"
    coresmith.mkdir()
    (coresmith / "interface_contracts.json").write_text(
        json.dumps(_contract_doc()), encoding="utf-8"
    )
    (coresmith / "architecture_state.json").write_text(json.dumps({
        "block_diagram": {"system_invariants": _invariants()},
    }), encoding="utf-8")
    top = tmp_path / "chip_top.v"
    top.write_text("module chip_top(input clk); endmodule\n", encoding="utf-8")
    captured = {}

    async def fake_generate(self, **kwargs):
        captured.update(kwargs)
        return {"tb_path": kwargs["output_path"], "test_count": 1}

    monkeypatch.setattr(IntegrationTestbenchGenerator, "generate", fake_generate)
    modules = {
        "spi_rom": VerilogModule("spi_rom", [
            VerilogPort("m_rsp_rsp_instr", "output", 16),
        ]),
        "mcu_core": VerilogModule("mcu_core", [
            VerilogPort("s_rsp_rsp_instr", "input", 16),
        ]),
    }
    await generate_integration_testbench(
        project_root=tmp_path,
        design_name="chip_top",
        top_rtl_path=str(top),
        modules=modules,
        connections=[],
        block_rtl_paths={},
    )

    assert captured["interface_contracts"]["contracts"][0]["edge_id"] == "rom_rsp"
    assert captured["system_invariants"][0]["id"] == "SYS-CHANNEL"


def test_semantic_loader_falls_back_to_block_diagram_file(tmp_path):
    coresmith = tmp_path / ".coresmith"
    coresmith.mkdir()
    (coresmith / "block_diagram.json").write_text(json.dumps({
        "system_invariants": _invariants(),
    }), encoding="utf-8")

    contracts, invariants = _load_integration_dv_semantics(tmp_path)

    assert contracts == {}
    assert invariants[0]["required_state"] == "outstanding_count <= 1"


def test_backpressure_requirement_is_conditioned_on_top_level_observability():
    assert "when the chip top exposes at least one corresponding" in SYSTEM_PROMPT
    assert "Never reach through DUT hierarchy" in SYSTEM_PROMPT
    assert "When backpressure is internal" in SYSTEM_PROMPT
    assert "Do not require one control merely because the" in SYSTEM_PROMPT
    assert "ordinary pre-edge AXI-Stream sampling at externally exposed" in SYSTEM_PROMPT
    assert "MANDATORY if AXI-Stream" not in SYSTEM_PROMPT
