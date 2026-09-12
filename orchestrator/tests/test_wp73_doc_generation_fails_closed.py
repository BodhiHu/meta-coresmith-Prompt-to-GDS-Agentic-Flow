# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""WP-73: a failed architecture-document generation stops the run; it never
returns a stub document that a later review can accept as the architecture."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


def _state(tmp_path):
    return {
        "project_root": str(tmp_path),
        "round": 1,
        "max_rounds": 3,
        "requirements": "a chip",
        "pdk_summary": "",
        "prd_spec": {"summary": "x"},
        "sad_spec": {"sad_text": "# SAD"},
        "frd_spec": {},
        "block_diagram": {"blocks": [{"name": "leaf"}], "connections": []},
        "human_response_history": [],
    }


def test_ers_node_raises_when_generation_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("CORESMITH_ERS_BEFORE_CONSTRAINTS", raising=False)
    from orchestrator.langgraph import architecture_graph as ag

    async def boom(**_kw):
        raise RuntimeError("provider exploded")

    with patch("orchestrator.architecture.specialists.ers_doc.generate_ers_doc", side_effect=boom):
        with pytest.raises(RuntimeError, match="ERS generation failed"):
            asyncio.run(ag.engineering_requirements_node(_state(tmp_path)))
    # no stub ERS was persisted
    ers = tmp_path / ".coresmith" / "ers_spec.json"
    assert not ers.exists() or "generation failed" not in ers.read_text()


def test_ers_specialist_raises_instead_of_stubbing(tmp_path):
    from orchestrator.architecture.specialists import ers_doc

    class _FailingLLM:
        def __init__(self, *_a, **_k):
            pass

        async def call(self, *_a, **_k):
            raise RuntimeError("argument list too long")

    with patch("orchestrator.langchain.agents.coresmith_llm.ClaudeLLM", _FailingLLM):
        with pytest.raises(RuntimeError, match="ERS generation failed"):
            asyncio.run(ers_doc.generate_ers_doc(
                prd_spec={"summary": "x"}, sad_spec={"sad_text": "# SAD"}, frd_spec={},
                block_diagram={"blocks": [{"name": "leaf"}], "connections": []},
                memory_map=None, clock_tree=None, register_spec=None,
                project_root=str(tmp_path),
            ))


def test_sad_specialist_raises_instead_of_stubbing(tmp_path):
    from orchestrator.architecture.specialists import sad_spec

    class _FailingLLM:
        def __init__(self, *_a, **_k):
            pass

        async def call(self, *_a, **_k):
            raise RuntimeError("argument list too long")

    with patch("orchestrator.langchain.agents.coresmith_llm.ClaudeLLM", _FailingLLM):
        with pytest.raises(RuntimeError, match="SAD generation failed"):
            asyncio.run(sad_spec.generate_sad(
                prd_spec={"summary": "x"}, requirements="a chip", pdk_summary="",
                project_root=str(tmp_path),
            ))
