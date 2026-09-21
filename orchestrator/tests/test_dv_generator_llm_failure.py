# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Disk-first DV generation must not adopt files from failed LLM calls."""

from __future__ import annotations

import pytest

from orchestrator.langchain.agents.coresmith_llm import is_llm_error_response
from orchestrator.langchain.agents.integration_testbench_generator import (
    IntegrationTestbenchGenerator,
)
from orchestrator.langchain.agents.validation_dv_generator import (
    ValidationDVGenerator,
)

VALIDATION_TB = '''import cocotb
REQUIREMENT_COVERAGE = {"REQ-1": {"status": "checked_by_test"}}

@cocotb.test()
async def check_output(dut):
    expected_output = 1
    assert dut.output.value == expected_output
'''

INTEGRATION_TB = '''import cocotb

@cocotb.test()
async def handles_error_words_as_normal_code(dut):
    error_message = "timeout is a behavior under test"
    assert error_message
'''


class _FakeLLM:
    def __init__(self, response: str):
        self.response = response

    async def call(self, **_kwargs):
        return self.response


def _validation_agent(response: str) -> ValidationDVGenerator:
    agent = object.__new__(ValidationDVGenerator)
    agent.llm = _FakeLLM(response)
    return agent


def _integration_agent(response: str) -> IntegrationTestbenchGenerator:
    agent = object.__new__(IntegrationTestbenchGenerator)
    agent.llm = _FakeLLM(response)
    return agent


def test_error_envelope_is_explicit_not_keyword_matching():
    assert is_llm_error_response("[ClaudeLLM error: incomplete]") is True
    assert is_llm_error_response(
        "```python\n# Handle timeout and error states\n```"
    ) is False


@pytest.mark.asyncio
async def test_validation_success_adopts_valid_disk_artifact(tmp_path):
    output = tmp_path / "validation.py"
    output.write_text(VALIDATION_TB)
    agent = _validation_agent("Successful tool turn; file written on disk.")

    result = await agent.generate(
        "chip_top", "chip_top.v", "module chip_top; endmodule", [], [],
        "REQ-1 must check output", output_path=str(output),
    )

    assert result["tb_path"] == str(output)
    assert output.read_text() == VALIDATION_TB


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "[ClaudeLLM error: OpenCode did not finish normally: incomplete]",
    "[ClaudeLLM error: OpenCode provider error: quota exhausted]",
])
async def test_validation_failure_rejects_existing_disk_artifact(
    tmp_path, failure,
):
    output = tmp_path / "validation.py"
    output.write_text(VALIDATION_TB)
    agent = _validation_agent(failure)

    with pytest.raises(RuntimeError, match="LLM call was unsuccessful"):
        await agent.generate(
            "chip_top", "chip_top.v", "module chip_top; endmodule", [], [],
            "REQ-1 must check output", output_path=str(output),
        )

    assert output.read_text() == VALIDATION_TB


@pytest.mark.asyncio
async def test_integration_success_adopts_valid_disk_artifact(tmp_path):
    output = tmp_path / "integration.py"
    output.write_text(INTEGRATION_TB)
    agent = _integration_agent("Successful tool turn; file written on disk.")

    result = await agent.generate(
        "chip_top", "module chip_top; endmodule", [], [],
        output_path=str(output),
    )

    assert result["tb_path"] == str(output)
    assert output.read_text() == INTEGRATION_TB


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "[ClaudeLLM error: OpenCode did not finish normally: incomplete]",
    "[ClaudeLLM error: OpenCode provider error: quota exhausted]",
])
async def test_integration_failure_rejects_existing_disk_artifact(
    tmp_path, failure,
):
    output = tmp_path / "integration.py"
    output.write_text(INTEGRATION_TB)
    agent = _integration_agent(failure)

    with pytest.raises(RuntimeError, match="LLM call was unsuccessful"):
        await agent.generate(
            "chip_top", "module chip_top; endmodule", [], [],
            output_path=str(output),
        )

    assert output.read_text() == INTEGRATION_TB
