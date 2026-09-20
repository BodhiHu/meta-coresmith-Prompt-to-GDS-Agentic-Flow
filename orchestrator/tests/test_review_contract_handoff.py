import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_review_and_rtl_author_get_identical_port_authority(tmp_path):
    from orchestrator.langchain.agents.integration_review_agent import IntegrationReviewAgent
    from orchestrator.langchain.agents.rtl_generator import build_user_message
    from orchestrator.langgraph.contract_conformance import format_contract_port_table

    specs = tmp_path / "arch" / "uarch_specs"
    specs.mkdir(parents=True)
    (specs / "core.md").write_text("# core specification")
    (tmp_path / ".coresmith").mkdir()
    contracts = {"contracts": [{
        "edge_id": "request", "producer_block": "core", "consumer_block": "spi",
        "producer_port": "fetch_request", "consumer_port": "fetch_request",
        "data_width_bits": 13,
        "fields": [{"name": "req_valid", "width": 1}, {"name": "req_pc", "width": 12}],
        "handshake_protocol": "valid_only",
    }]}
    (tmp_path / ".coresmith" / "interface_contracts.json").write_text(json.dumps(contracts))
    agent = IntegrationReviewAgent()
    agent.llm.call = AsyncMock(return_value='{"issues_found": 0, "issues_fixed": 0}')
    await agent.review(["core"], str(tmp_path))
    review_prompt = agent.llm.call.call_args.kwargs["prompt"]
    rtl_prompt = build_user_message(block_name="core", project_root=str(tmp_path))
    table = format_contract_port_table(str(tmp_path), "core")
    assert table and "fetch_request_req_pc" in table
    assert table in review_prompt and table in rtl_prompt
    assert "CANONICAL INTERFACE CONTRACTS" in review_prompt
    assert "valid_only" in review_prompt


@pytest.mark.asyncio
async def test_no_uarch_check_call_before_specs_exist(tmp_path, monkeypatch):
    from orchestrator.architecture.constraints import check_constraints

    call = AsyncMock(return_value='{"pass":true,"evidence":"ok"}')
    monkeypatch.setattr("orchestrator.langchain.agents.coresmith_llm.ClaudeLLM.call", call)
    contracts = {"contracts": [{"edge_id": "request", "producer_block": "core",
                                 "consumer_block": "spi"}]}
    await check_constraints({}, {}, {}, {}, project_root=str(tmp_path),
                            interface_contracts=contracts)
    names = [c.kwargs["run_name"] for c in call.call_args_list]
    assert "constraint_subagent:cross_spec_contract_adherence" not in names


def _seed_cross_tier_review(root):
    specs = root / "arch" / "uarch_specs"
    specs.mkdir(parents=True)
    (specs / "core.md").write_text("# current CPU spec")
    (specs / "spi.md").write_text("# previously verified SPI spec")
    (root / ".coresmith").mkdir()
    diagram = {
        "blocks": [{"name": n} for n in ("core", "spi", "future")],
        "connections": [
            {"from": "core.req", "to": "spi.req"},
            {"from": "spi.rsp", "to": "core.rsp"},
            {"from": "core.out", "to": "future.in"},
        ],
    }
    (root / ".coresmith" / "block_diagram.json").write_text(json.dumps(diagram))
    return diagram


@pytest.mark.asyncio
async def test_review_includes_available_cross_tier_edges_without_adopting_neighbor(tmp_path):
    from orchestrator.langchain.agents.integration_review_agent import IntegrationReviewAgent

    diagram = _seed_cross_tier_review(tmp_path)
    agent = IntegrationReviewAgent()

    async def review(**kwargs):
        prompt = kwargs["prompt"]
        context = tmp_path / "arch/uarch_specs_review/context/spi.md"
        assert str(context) in prompt and "read-only" in prompt.lower()
        assert context.read_text() == "# previously verified SPI spec"
        filtered = json.loads((tmp_path / ".coresmith/integration_review_block_diagram.json").read_text())
        assert filtered["connections"] == diagram["connections"][:2]
        assert {b["name"] for b in filtered["blocks"]} == {"core", "spi"}
        return '{"issues_found": 0, "issues_fixed": 0}'

    agent.llm.call = AsyncMock(side_effect=review)
    result = await agent.review(["core"], str(tmp_path))
    assert set(result["reviewed_specs"]) == {"core"}
    assert result["edited_blocks"] == []
    assert (tmp_path / "arch/uarch_specs/spi.md").read_text() == "# previously verified SPI spec"


@pytest.mark.asyncio
@pytest.mark.parametrize("relative", [
    "arch/uarch_specs_review/context/spi.md", "arch/uarch_specs/spi.md",
])
async def test_review_rejects_mutated_adjacent_spec(tmp_path, relative):
    from orchestrator.langchain.agents.integration_review_agent import IntegrationReviewAgent

    _seed_cross_tier_review(tmp_path)
    agent = IntegrationReviewAgent()

    async def corrupt_context(**kwargs):
        Path(tmp_path / relative).write_text("unverified replacement")
        return '{"issues_found": 0, "issues_fixed": 0}'

    agent.llm.call = AsyncMock(side_effect=corrupt_context)
    with pytest.raises(ValueError, match="read-only adjacent spec"):
        await agent.review(["core"], str(tmp_path))
